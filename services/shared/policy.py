"""
cloudx central policy engine
============================
One rule per action (`DEFAULT_RULES`), optionally overridden by a YAML file (`CLOUDX_POLICY_FILE`,
default /etc/cloudx/policy.yaml). Rules are written in the language of shared.policy_rules.

The file is read on first use and re-read when it changes (mtime, ctime, size or inode) (checked at most every 2 seconds), so
an edit needs no restart. A bad file or a bad rule never stops a service: the affected rule keeps its
default (or, for a file that cannot be read at all, the last good rules), and one ERROR line per
(re)load of a changed file goes to the `cloudx-policy` logger.

All rules are validated when the file is loaded (references exist, no cycles, bounded nesting), so
evaluating a request never has to cope with a broken rule.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable, NamedTuple

import yaml
from fastapi import Depends, HTTPException

from .policy_rules import Expr, RuleError, evaluate, parse, references

_log = logging.getLogger("cloudx-policy")

_ADMIN_OR_DOMAIN_ADMIN = "role:admin or domain_admin:True"
_ADMIN = "role:admin"

DEFAULT_RULES: dict[str, str] = {
    **{
        action: _ADMIN_OR_DOMAIN_ADMIN
        for action in (
            "identity:user:create",
            "identity:user:list",
            "identity:user:delete",
            "identity:domain:list",
            "identity:domain:show",
            "identity:domain:quota-show",
            "identity:project:create",
            "identity:project:update",
            "identity:project:delete",
            "identity:role-assignment:create",
            "identity:role-assignment:delete",
        )
    },
    **{
        action: _ADMIN
        for action in (
            "identity:domain:create",
            "identity:domain:update",
            "identity:domain:delete",
            "identity:domain:quota-set",
            "identity:domain:quota-clear",
            "identity:service:create",
            "identity:service:delete",
            "identity:endpoint:create",
            "identity:endpoint:delete",
            "identity:project:quota-set",
            "identity:audit-log:list",
        )
    },
}

CHECK_INTERVAL = 2.0  # seconds between two stat() calls of the policy file
MAX_REFERENCE_DEPTH = 10  # rule:NAME hops in a row
MAX_EVAL_DEPTH = 500  # evaluator frames, summed over those hops; keeps evaluate() far from the recursion limit

_monotonic = time.monotonic  # tests patch this name

_DEFAULT_EXPRS: dict[str, Expr] = {action: parse(rule) for action, rule in DEFAULT_RULES.items()}


def POLICY_FILE() -> str:
    return os.environ.get("CLOUDX_POLICY_FILE", "/etc/cloudx/policy.yaml")


class _Snapshot(NamedTuple):
    rules: dict[str, str]  # action -> effective rule text
    sources: dict[str, str]  # action -> "default" | "file"
    exprs: dict[str, Expr]  # every effective rule by name: the actions and the file's named rules


_DEFAULTS = _Snapshot(
    rules=dict(DEFAULT_RULES),
    sources={action: "default" for action in DEFAULT_RULES},
    exprs=dict(_DEFAULT_EXPRS),
)


class _Cache(NamedTuple):
    snapshot: _Snapshot
    path: str
    stamp: tuple | None  # _stat() of the file when it was last (re)loaded; None: no file
    checked: float  # _monotonic() of the last stat()


_cache: _Cache | None = None
_lock = threading.Lock()


def reset() -> None:
    """Forget the loaded rules, the remembered file stamp and the clock (tests)."""
    global _cache
    with _lock:
        _cache = None


# ── validation ────────────────────────────────────────────────────────────────

def _expr_depth(expr: Expr) -> int:
    if expr[0] in ("not", "and", "or"):
        return 1 + max(_expr_depth(child) for child in expr[1:])
    return 1


def _find_problems(exprs: dict[str, Expr], dropped: set[str]) -> dict[str, str]:
    """name -> reason, for every rule that cannot be evaluated safely as it stands.

    Iterative depth-first walk (a hostile file may chain thousands of rules). A rule that only *uses* a
    faulty rule is not reported here: once the faulty one is dropped, the caller runs this again.
    """
    refs = {name: sorted(references(expr)) for name, expr in exprs.items()}
    problems: dict[str, str] = {}
    visiting: set[str] = set()
    done: set[str] = set()
    hops: dict[str, int] = {}
    cost: dict[str, int] = {}
    for root in sorted(exprs):
        if root in done or root in visiting:
            continue
        path = [root]
        iters = [iter(refs[root])]
        visiting.add(root)
        while path:
            name = path[-1]
            descended = False
            for ref in iters[-1]:
                if ref not in exprs:
                    if ref in dropped:
                        problems.setdefault(name, f"uses rule '{ref}', which has an error")
                    else:
                        problems.setdefault(name, f"references unknown rule '{ref}'")
                elif ref in visiting:
                    for member in path[path.index(ref):]:
                        problems.setdefault(member, f"rules reference each other in a cycle (through '{ref}')")
                elif ref not in done:
                    visiting.add(ref)
                    path.append(ref)
                    iters.append(iter(refs[ref]))
                    descended = True
                    break
            if descended:
                continue
            path.pop()
            iters.pop()
            visiting.discard(name)
            done.add(name)
            if name in problems:
                continue
            below = [r for r in refs[name] if r in hops]
            hops[name] = 1 + max(hops[r] for r in below) if below else 0
            cost[name] = _expr_depth(exprs[name]) + (1 + max(cost[r] for r in below) if below else 0)
            if hops[name] > MAX_REFERENCE_DEPTH:
                problems[name] = f"rule references are nested more than {MAX_REFERENCE_DEPTH} deep"
                del hops[name], cost[name]
            elif cost[name] > MAX_EVAL_DEPTH:
                problems[name] = "rule is too deeply nested to evaluate"
                del hops[name], cost[name]
    return problems


def _emit(problems: list[str] | None, level: int, fmt: str, *args, exc_info: bool = False) -> None:
    """Report one problem: to the `cloudx-policy` logger, or, when `problems` is given, only to that list.

    The list gets the line exactly as the logger would print it, so `check_file` and the engine cannot
    drift apart; a check never logs.
    """
    if problems is None:
        _log.log(level, fmt, *args, exc_info=exc_info, stacklevel=2)
    else:
        problems.append(fmt % args)


def _compile(data: dict, problems: list[str] | None = None) -> _Snapshot:
    """Effective rules from a parsed file; logs every rule error and warning (or appends them to `problems`)."""
    parsed: dict[str, tuple[str, Expr]] = {}  # file entries that parse
    dropped: set[str] = set()

    def reject(name: str, reason: str) -> None:
        dropped.add(name)
        if name in DEFAULT_RULES:
            _emit(problems, logging.ERROR, "policy file: rule for '%s' ignored, the default applies: %s", name, reason)
        else:
            _emit(problems, logging.ERROR, "policy file: rule '%s' ignored: %s", name, reason)

    for key, value in data.items():
        if not isinstance(key, str):
            _emit(problems, logging.ERROR, "policy file: entry %r ignored: names must be strings", key)
        elif not isinstance(value, str):
            reject(key, f"the value {value!r} is not a string; put the rule in quotes, for example '{key}: \"role:admin\"'")
        else:
            try:
                parsed[key] = (value, parse(value))
            except RuleError as exc:
                reject(key, str(exc))

    while True:
        exprs = {**_DEFAULT_EXPRS, **{name: expr for name, (_, expr) in parsed.items()}}
        faulty = {name: reason for name, reason in _find_problems(exprs, dropped).items() if name in parsed}
        if not faulty:
            break
        for name, reason in faulty.items():
            reject(name, reason)
            del parsed[name]

    used = set().union(*(references(expr) for expr in exprs.values()))
    for name in parsed:
        if name not in DEFAULT_RULES and name not in used:
            _emit(problems, logging.WARNING, "policy file: '%s' is not an action and no rule uses it (typo?)", name)

    return _Snapshot(
        rules={action: parsed[action][0] if action in parsed else rule for action, rule in DEFAULT_RULES.items()},
        sources={action: "file" if action in parsed else "default" for action in DEFAULT_RULES},
        exprs=exprs,
    )


# ── file handling ─────────────────────────────────────────────────────────────

def _stat(path: str) -> tuple | None:
    """What identifies the file's current state; None when there is no file.

    ctime moves on chmod/chown (a permission fix must be noticed) and on every write; the inode catches
    an atomic replace that kept the old mtime (install -p, rsync -t); the size catches a truncation.
    """
    try:
        st = os.stat(path)
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError:
        return ("unexaminable",)  # differs from every real stamp, so load() reports the cause
    return (st.st_mtime_ns, st.st_ctime_ns, st.st_size, st.st_ino)


def _load(path: str, problems: list[str] | None = None) -> _Snapshot | None:
    """The rules of the file, or None (after logging an ERROR) when the file as a whole is unusable.

    With `problems`, nothing is logged: each line that would have been is appended to the list instead.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except FileNotFoundError:
        return _DEFAULTS  # vanished between stat() and open()
    except (OSError, UnicodeError) as exc:
        _emit(problems, logging.ERROR, "policy file %s cannot be read: %s", path, exc)
        return None
    try:
        data = yaml.safe_load(text)
    except Exception as exc:  # yaml.YAMLError, but also RecursionError and the like on hostile input
        _emit(problems, logging.ERROR, "policy file %s is not valid YAML: %s", path, exc)
        return None
    if data is None:
        return _DEFAULTS
    if not isinstance(data, dict):
        _emit(problems, logging.ERROR, "policy file %s must be a mapping of action to rule, not a %s", path, type(data).__name__)
        return None
    try:
        return _compile(data, problems)
    except Exception as exc:  # a service must never fail because of this file; the cause is logged with its traceback
        _emit(problems, logging.ERROR, "policy file %s could not be applied%s", path,
              "" if problems is None else f": {exc!r}", exc_info=True)
        return None


def _current() -> _Snapshot:
    """The effective rules, re-checking the file when due."""
    global _cache
    path = POLICY_FILE()
    cache = _cache
    if cache is not None and cache.path == path and _monotonic() - cache.checked < CHECK_INTERVAL:
        return cache.snapshot
    with _lock:
        cache = _cache
        now = _monotonic()
        same_file = cache is not None and cache.path == path
        if same_file and now - cache.checked < CHECK_INTERVAL:
            return cache.snapshot
        stamp = _stat(path)
        if same_file and stamp == cache.stamp:
            _cache = cache._replace(checked=now)
            return cache.snapshot
        snapshot = _DEFAULTS if stamp is None else _load(path)
        if snapshot is None:  # unusable file: keep the last good rules (the defaults on a first load)
            snapshot = cache.snapshot if same_file else _DEFAULTS
        # the stamp is remembered even after a failure, so a broken file is not re-parsed and re-logged
        _cache = _Cache(snapshot, path, stamp, now)
        return snapshot


# ── public API ────────────────────────────────────────────────────────────────

def effective() -> tuple[dict[str, str], dict[str, str]]:
    """(rules, sources) of one snapshot, so a caller never sees the two from different loads."""
    snapshot = _current()
    return dict(snapshot.rules), dict(snapshot.sources)


def rules() -> dict[str, str]:
    """Effective rule text for each of the actions."""
    return dict(_current().rules)


def sources() -> dict[str, str]:
    """For each action: 'file' when the policy file's rule is in effect, else 'default'."""
    return dict(_current().sources)


def authorize(action: str, token: dict) -> bool:
    """Whether `token` may perform `action`. KeyError for an action that does not exist."""
    if action not in DEFAULT_RULES:
        raise KeyError(action)
    exprs = _current().exprs

    def resolve(name: str) -> bool:
        expr = exprs.get(name)
        return False if expr is None else evaluate(expr, token, resolve)

    return evaluate(exprs[action], token, resolve)


# ── operator tools (cloudx-manage policy) ─────────────────────────────────────

def check_file(path: str | None = None) -> list[str]:
    """One line per problem the engine would log when loading `path` (default: the configured file).

    A missing, empty or comment-only file has none. Pure: the live rules and the log are left alone.
    """
    problems: list[str] = []
    _load(path or POLICY_FILE(), problems)
    return problems


def overrides(path: str | None = None) -> dict[str, str]:
    """action -> rule text, for the actions whose rule in the file is in effect and differs from the default.

    An action whose rule had an error keeps its default and is not listed.
    """
    snapshot = _load(path or POLICY_FILE(), [])
    if snapshot is None:
        return {}
    return {action: rule for action, rule in snapshot.rules.items() if rule != DEFAULT_RULES[action]}


def differing(path: str | None = None) -> list[str]:
    """The actions whose rule in the file differs from the default."""
    return list(overrides(path))


_HEADER = """\
# CloudX policy file. Install it as /etc/cloudx/policy.yaml (mode 640, owner root, group cloudx).
#
# Everything below is commented out: the built-in rules apply. List only the actions you want to
# change, as "action: rule" lines. Copy a line, remove the "# " and edit the rule. Quote every rule;
# a rule that starts with ! or @ must be quoted ("!" and "@"), or the file is not valid YAML.
#
# A rule is a boolean expression:
#
#   role:NAME                      the token's role is NAME
#   domain_admin:True              the token carries the domain-admin flag (False: it does not)
#   rule:NAME                      the result of another rule of this file
#   @                              always true
#   !                              always false
#   and, or, not, ( )              combine them; not binds tightest, then and, then or
#
# An entry whose name is not an action is a named rule that other rules can use as rule:NAME.
# A named rule that nothing uses is reported as a probable typo.
#
# A bad rule is ignored and the action keeps its default; a bad file never stops a service. The
# file is re-read when it changes, no restart needed. It must exist, identical, on every node that
# runs the service. Check it before you deploy it:
#
#   cloudx-manage policy check FILE
#
# The built-in rules:
#
"""


def defaults_yaml() -> str:
    """The built-in table as a commented YAML file: the header, then one `# action: "rule"` line per action."""
    return _HEADER + "".join(f'# {action}: "{rule}"\n' for action, rule in DEFAULT_RULES.items())


_required: set[str] = set()


def require(action: str, authenticate: Callable | None = None) -> Callable:
    """FastAPI dependency that lets a request through only when the policy allows `action`.

    `authenticate` is the dependency that yields the token payload (default: shared.auth_middleware.
    require_auth). It runs first, so its 401 is never turned into a 403. The dependency returns the payload.
    KeyError at once (not at request time) for an action that does not exist.
    """
    if action not in DEFAULT_RULES:
        raise KeyError(action)
    if authenticate is None:
        # lazy: auth_middleware reads AUTH_SECRET_KEY at import, and this module must import without it
        from .auth_middleware import require_auth as authenticate
    _required.add(action)

    def dependency(token: dict = Depends(authenticate)) -> dict:
        if not authorize(action, token):
            raise HTTPException(status_code=403, detail=f"Policy does not allow {action}")
        return token

    return dependency


def required_actions() -> set[str]:
    """Every action passed to require() in this process (a copy)."""
    return set(_required)

