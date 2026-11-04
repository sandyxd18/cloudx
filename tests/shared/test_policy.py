import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "services"))

from shared import policy  # noqa: E402

ADMIN = {"role": "admin"}
DOMAIN_ADMIN = {"role": "member", "domain_admin": True}
MEMBER = {"role": "member"}

ADMIN_OR_DOMAIN_ADMIN_ACTIONS = {
    "identity:user:create", "identity:user:list", "identity:user:delete",
    "identity:domain:list", "identity:domain:show", "identity:domain:quota-show",
    "identity:project:create", "identity:project:update", "identity:project:delete",
    "identity:role-assignment:create", "identity:role-assignment:delete",
}
ADMIN_ONLY_ACTIONS = {
    "identity:domain:create", "identity:domain:update", "identity:domain:delete",
    "identity:domain:quota-set", "identity:domain:quota-clear",
    "identity:service:create", "identity:service:delete",
    "identity:endpoint:create", "identity:endpoint:delete",
    "identity:project:quota-set", "identity:audit-log:list",
}


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class PolicyFile:
    """A policy file in tmp_path; every write gets a distinct mtime so tests never sleep."""

    def __init__(self, path):
        self.path = path
        self._tick = 0

    def write(self, text):
        self.path.write_text(text, encoding="utf-8")
        self._tick += 1
        stamp = 10**18 + self._tick * 10**9
        os.utime(self.path, ns=(stamp, stamp))

    def remove(self):
        self.path.unlink()


@pytest.fixture(autouse=True)
def log_level(caplog):
    caplog.set_level(logging.DEBUG, logger="cloudx-policy")


@pytest.fixture
def pfile(tmp_path, monkeypatch):
    path = tmp_path / "policy.yaml"
    monkeypatch.setenv("CLOUDX_POLICY_FILE", str(path))
    policy.reset()
    yield PolicyFile(path)
    policy.reset()


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(policy, "_monotonic", c)
    return c


def messages(caplog, level):
    return [r.getMessage() for r in caplog.records if r.name == "cloudx-policy" and r.levelno == level]


def no_logs(caplog):
    return [r for r in caplog.records if r.name == "cloudx-policy"] == []


def test_the_22_identity_actions_of_the_spec_are_unchanged_inside_the_57():
    expected = {a: "role:admin or domain_admin:True" for a in ADMIN_OR_DOMAIN_ADMIN_ACTIONS}
    expected.update({a: "role:admin" for a in ADMIN_ONLY_ACTIONS})
    assert len(expected) == 22
    identity = {a: r for a, r in policy.DEFAULT_RULES.items() if a.startswith("identity:")}
    assert identity == expected
    assert len(policy.DEFAULT_RULES) == 57


def test_policy_file_default_path_and_env_read_at_call_time(monkeypatch):
    monkeypatch.delenv("CLOUDX_POLICY_FILE", raising=False)
    assert policy.POLICY_FILE() == "/etc/cloudx/policy.yaml"
    monkeypatch.setenv("CLOUDX_POLICY_FILE", "/x/y.yaml")
    assert policy.POLICY_FILE() == "/x/y.yaml"


def test_no_file_gives_defaults_and_logs_nothing(pfile, caplog):
    assert policy.rules() == policy.DEFAULT_RULES
    assert set(policy.sources().values()) == {"default"}
    assert set(policy.sources()) == set(policy.DEFAULT_RULES)
    assert policy.authorize("identity:domain:create", ADMIN) is True
    assert policy.authorize("identity:domain:create", DOMAIN_ADMIN) is False
    assert no_logs(caplog)


def test_rules_returns_a_copy(pfile):
    policy.rules()["identity:domain:create"] = "@"
    assert policy.rules()["identity:domain:create"] == "role:admin"


def test_file_overrides_one_action_only(pfile, caplog):
    pfile.write("identity:domain:create: role:admin or domain_admin:True\n")
    rules = policy.rules()
    assert rules["identity:domain:create"] == "role:admin or domain_admin:True"
    assert {a: r for a, r in rules.items() if a != "identity:domain:create"} == {
        a: r for a, r in policy.DEFAULT_RULES.items() if a != "identity:domain:create"
    }
    sources = policy.sources()
    assert sources["identity:domain:create"] == "file"
    assert {s for a, s in sources.items() if a != "identity:domain:create"} == {"default"}
    assert policy.authorize("identity:domain:create", DOMAIN_ADMIN) is True
    assert no_logs(caplog)


def test_empty_file_means_no_overrides_and_logs_nothing(pfile, caplog):
    pfile.write("")
    assert policy.rules() == policy.DEFAULT_RULES
    pfile.write("# only a comment\n\n")
    policy.reset()
    assert policy.rules() == policy.DEFAULT_RULES
    assert no_logs(caplog)


def test_null_document_means_no_overrides(pfile, caplog):
    pfile.write("---\n")
    assert policy.rules() == policy.DEFAULT_RULES
    pfile.write("null\n")
    policy.reset()
    assert policy.rules() == policy.DEFAULT_RULES
    assert no_logs(caplog)


@pytest.mark.parametrize("text", ["- identity:domain:create\n- role:admin\n", "just a string\n", "42\n"])
def test_top_level_that_is_not_a_mapping_keeps_defaults_and_logs_error(pfile, caplog, text):
    pfile.write(text)
    assert policy.rules() == policy.DEFAULT_RULES
    assert set(policy.sources().values()) == {"default"}
    errors = messages(caplog, logging.ERROR)
    assert len(errors) == 1 and "mapping" in errors[0]


def test_non_string_rule_is_a_rule_error_for_that_action(pfile, caplog):
    pfile.write("identity:user:create: true\nidentity:domain:create: 'role:admin or domain_admin:True'\n")
    assert policy.rules()["identity:user:create"] == policy.DEFAULT_RULES["identity:user:create"]
    assert policy.sources()["identity:user:create"] == "default"
    assert policy.sources()["identity:domain:create"] == "file"
    errors = messages(caplog, logging.ERROR)
    assert len(errors) == 1
    assert "identity:user:create" in errors[0] and "quote" in errors[0]


@pytest.mark.parametrize("value", ["1", "[role:admin]", "null", "~"])
def test_other_non_string_values_are_rule_errors(pfile, caplog, value):
    pfile.write(f"identity:user:create: {value}\n")
    assert policy.rules() == policy.DEFAULT_RULES
    assert len(messages(caplog, logging.ERROR)) == 1


def test_non_string_key_is_ignored_with_an_error(pfile, caplog):
    pfile.write("123: role:admin\nidentity:domain:create: '@'\n")
    assert policy.sources()["identity:domain:create"] == "file"
    errors = messages(caplog, logging.ERROR)
    assert len(errors) == 1 and "123" in errors[0]


def test_rule_error_keeps_only_that_actions_default(pfile, caplog):
    pfile.write(
        "identity:user:create: 'role:admin and'\n"
        "identity:domain:create: 'role:admin or domain_admin:True'\n"
    )
    assert policy.rules()["identity:user:create"] == policy.DEFAULT_RULES["identity:user:create"]
    assert policy.sources()["identity:user:create"] == "default"
    assert policy.sources()["identity:domain:create"] == "file"
    assert policy.authorize("identity:domain:create", DOMAIN_ADMIN) is True
    errors = messages(caplog, logging.ERROR)
    assert len(errors) == 1 and "identity:user:create" in errors[0]


def test_unknown_reference_is_a_rule_error(pfile, caplog):
    pfile.write("identity:user:create: 'rule:nope'\n")
    assert policy.rules() == policy.DEFAULT_RULES
    errors = messages(caplog, logging.ERROR)
    assert len(errors) == 1 and "identity:user:create" in errors[0] and "nope" in errors[0]


def test_cycle_is_a_rule_error(pfile, caplog):
    pfile.write("a: 'rule:b'\nb: 'rule:a'\nidentity:user:create: 'rule:a'\n")
    assert policy.rules() == policy.DEFAULT_RULES
    assert policy.authorize("identity:user:create", ADMIN) is True
    assert policy.authorize("identity:user:create", MEMBER) is False
    errors = messages(caplog, logging.ERROR)
    assert any("rule 'a'" in e and "cycle" in e for e in errors)
    assert any("rule 'b'" in e and "cycle" in e for e in errors)
    # the entries that only existed to be referenced are not warned about as unreferenced
    assert messages(caplog, logging.WARNING) == []


def test_action_cycle_through_actions_falls_back_for_every_member(pfile, caplog):
    pfile.write("identity:user:create: 'rule:identity:user:delete'\nidentity:user:delete: 'rule:identity:user:create'\n")
    assert policy.rules() == policy.DEFAULT_RULES
    assert len(messages(caplog, logging.ERROR)) == 2


def test_self_reference_is_a_cycle(pfile, caplog):
    pfile.write("identity:user:create: 'rule:identity:user:create'\n")
    assert policy.rules() == policy.DEFAULT_RULES
    assert len(messages(caplog, logging.ERROR)) == 1


def _chain(hops):
    # identity:user:create -> r1 -> ... -> r<hops> -> role:admin, i.e. `hops` references in a row
    lines = ["identity:user:create: 'rule:r1'"]
    lines += [f"r{i}: 'rule:r{i + 1}'" for i in range(1, hops)]
    lines.append(f"r{hops}: 'role:admin'")
    return "\n".join(lines) + "\n"


def test_a_chain_of_11_references_is_a_rule_error(pfile, caplog):
    pfile.write(_chain(11))
    assert policy.sources()["identity:user:create"] == "default"
    errors = messages(caplog, logging.ERROR)
    assert len(errors) == 1 and "identity:user:create" in errors[0] and "deep" in errors[0]


def test_a_chain_of_10_references_is_fine(pfile, caplog):
    pfile.write(_chain(10))
    assert policy.sources()["identity:user:create"] == "file"
    assert policy.authorize("identity:user:create", ADMIN) is True
    assert policy.authorize("identity:user:create", DOMAIN_ADMIN) is False
    assert messages(caplog, logging.ERROR) == []


def test_very_long_chain_of_named_rules_does_not_crash(pfile, caplog):
    n = 5000
    lines = [f"r{i}: 'rule:r{i + 1}'" for i in range(n)] + [f"r{n}: 'role:admin'"]
    lines.append("identity:user:create: 'rule:r0'")
    pfile.write("\n".join(lines) + "\n")
    assert policy.rules() == policy.DEFAULT_RULES
    assert policy.authorize("identity:user:create", ADMIN) is True


def test_deeply_nested_rule_through_many_references_cannot_overflow_the_stack(pfile, caplog):
    deep = "not " * 190 + "role:admin"
    lines = [f"r{i}: '{deep.replace('role:admin', f'rule:r{i + 1}')}'" for i in range(1, 9)]
    lines.append("r9: 'role:admin'")
    lines.append("identity:user:create: 'rule:r1'")
    pfile.write("\n".join(lines) + "\n")
    # whatever is decided about these rules, evaluating must not raise
    policy.authorize("identity:user:create", ADMIN)
    policy.authorize("identity:user:create", MEMBER)


def test_named_rule_can_be_referenced(pfile, caplog):
    pfile.write('admin_required: "role:admin"\nidentity:user:create: "rule:admin_required"\n')
    assert policy.sources()["identity:user:create"] == "file"
    assert policy.rules()["identity:user:create"] == "rule:admin_required"
    assert policy.authorize("identity:user:create", ADMIN) is True
    assert policy.authorize("identity:user:create", DOMAIN_ADMIN) is False
    assert no_logs(caplog)


def test_a_rule_can_reference_another_action(pfile):
    pfile.write('identity:project:create: "rule:identity:domain:create"\n')
    assert policy.authorize("identity:project:create", ADMIN) is True
    assert policy.authorize("identity:project:create", DOMAIN_ADMIN) is False


def test_dropped_named_rule_invalidates_the_rules_that_use_it(pfile, caplog):
    pfile.write(
        "base: 'role:admin and'\n"
        "mid: 'rule:base'\n"
        "identity:user:create: 'rule:mid'\n"
        "identity:domain:create: '@'\n"
    )
    assert policy.sources()["identity:user:create"] == "default"
    assert policy.sources()["identity:domain:create"] == "file"
    errors = messages(caplog, logging.ERROR)
    assert any("base" in e for e in errors)
    assert any("mid" in e for e in errors)
    assert any("identity:user:create" in e for e in errors)
    assert messages(caplog, logging.WARNING) == []


def test_action_falling_back_does_not_break_rules_that_reference_it(pfile):
    pfile.write(
        "identity:user:create: 'rule:nope'\n"
        "identity:user:delete: 'rule:identity:user:create'\n"
    )
    assert policy.sources()["identity:user:create"] == "default"
    assert policy.sources()["identity:user:delete"] == "file"
    assert policy.authorize("identity:user:delete", DOMAIN_ADMIN) is True
    assert policy.authorize("identity:user:delete", MEMBER) is False


def test_unreferenced_unknown_entry_logs_warning(pfile, caplog):
    pfile.write("identity:user:creat: 'role:admin'\n")
    assert policy.rules() == policy.DEFAULT_RULES
    warnings = messages(caplog, logging.WARNING)
    assert len(warnings) == 1 and "identity:user:creat" in warnings[0]
    assert messages(caplog, logging.ERROR) == []


def test_referenced_named_rule_logs_no_warning(pfile, caplog):
    pfile.write('helper: "role:admin"\nidentity:user:create: "rule:helper"\n')
    assert messages(caplog, logging.WARNING) == []
    policy.rules()
    assert no_logs(caplog)


@pytest.mark.parametrize("text", ["identity:domain:create: !\n", "identity:domain:create: @\n"])
def test_unquoted_special_characters_never_change_the_decision(pfile, caplog, text):
    pfile.write(text)
    assert policy.authorize("identity:domain:create", ADMIN) is True
    assert policy.authorize("identity:domain:create", DOMAIN_ADMIN) is False
    assert policy.authorize("identity:domain:create", MEMBER) is False
    assert policy.rules() == policy.DEFAULT_RULES
    assert messages(caplog, logging.ERROR)


def test_invalid_yaml_keeps_defaults_and_logs_error(pfile, caplog):
    pfile.write("identity:domain:create: [unclosed\n")
    assert policy.rules() == policy.DEFAULT_RULES
    assert len(messages(caplog, logging.ERROR)) == 1


def test_unreadable_file_keeps_defaults_and_logs_error(pfile, caplog, monkeypatch):
    pfile.write("identity:domain:create: '@'\n")
    real_open = open

    def refuse(path, *a, **k):
        if str(path) == str(pfile.path):
            raise PermissionError("denied")
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", refuse)
    assert policy.rules() == policy.DEFAULT_RULES
    assert len(messages(caplog, logging.ERROR)) == 1


def test_file_that_is_not_utf8_keeps_defaults_and_logs_error(pfile, caplog):
    pfile.path.write_bytes(b"identity:domain:create: '\xff\xfe'\n")
    assert policy.rules() == policy.DEFAULT_RULES
    assert len(messages(caplog, logging.ERROR)) == 1


def test_yaml_tags_are_not_executed(pfile, caplog):
    pfile.write("identity:domain:create: !!python/object/apply:os.getcwd []\n")
    assert policy.rules() == policy.DEFAULT_RULES
    assert len(messages(caplog, logging.ERROR)) == 1


def test_a_failed_reload_keeps_the_last_good_rules(pfile, clock, caplog):
    pfile.write("identity:domain:create: 'role:admin or domain_admin:True'\n")
    assert policy.authorize("identity:domain:create", DOMAIN_ADMIN) is True
    pfile.write("identity:domain:create: [unclosed\n")
    clock.advance(3)
    assert policy.authorize("identity:domain:create", DOMAIN_ADMIN) is True
    assert policy.sources()["identity:domain:create"] == "file"
    assert len(messages(caplog, logging.ERROR)) == 1


def test_a_failed_reload_is_logged_once_not_on_every_check(pfile, clock, caplog):
    pfile.write("identity:domain:create: '@'\n")
    policy.rules()
    pfile.write("- not a mapping\n")
    for _ in range(5):
        clock.advance(3)
        policy.rules()
    assert len(messages(caplog, logging.ERROR)) == 1
    pfile.write("{broken\n")
    clock.advance(3)
    policy.rules()
    assert len(messages(caplog, logging.ERROR)) == 2


def test_a_valid_file_after_a_broken_one_is_picked_up(pfile, clock):
    pfile.write("{broken\n")
    assert policy.rules() == policy.DEFAULT_RULES
    pfile.write("identity:domain:create: '@'\n")
    clock.advance(3)
    assert policy.rules()["identity:domain:create"] == "@"


def test_mtime_change_is_picked_up_without_restart(pfile, clock):
    assert policy.authorize("identity:domain:create", DOMAIN_ADMIN) is False
    pfile.write("identity:domain:create: 'role:admin or domain_admin:True'\n")
    clock.advance(3)
    assert policy.authorize("identity:domain:create", DOMAIN_ADMIN) is True
    pfile.write("identity:domain:create: 'role:admin'\n")
    clock.advance(3)
    assert policy.authorize("identity:domain:create", DOMAIN_ADMIN) is False


def test_mtime_is_checked_at_most_every_2_seconds(pfile, clock):
    pfile.write("identity:domain:create: 'role:admin'\n")
    assert policy.authorize("identity:domain:create", DOMAIN_ADMIN) is False
    pfile.write("identity:domain:create: 'role:admin or domain_admin:True'\n")
    clock.advance(1.9)
    assert policy.authorize("identity:domain:create", DOMAIN_ADMIN) is False
    clock.advance(0.2)
    assert policy.authorize("identity:domain:create", DOMAIN_ADMIN) is True


def test_unchanged_mtime_is_not_reparsed_or_relogged(pfile, clock, caplog):
    pfile.write("identity:user:creat: 'role:admin'\n")
    for _ in range(4):
        policy.rules()
        clock.advance(3)
    assert len(messages(caplog, logging.WARNING)) == 1


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read a mode-0 file")
def test_a_permission_fix_is_noticed_although_the_mtime_did_not_change(pfile, clock, caplog):
    pfile.write("identity:domain:create: 'role:admin or domain_admin:True'\n")
    mtime = os.stat(pfile.path).st_mtime_ns
    os.chmod(pfile.path, 0)
    assert policy.rules() == policy.DEFAULT_RULES
    assert len(messages(caplog, logging.ERROR)) == 1
    os.chmod(pfile.path, 0o644)
    os.utime(pfile.path, ns=(mtime, mtime))  # chmod leaves mtime alone; make sure of it
    clock.advance(3)
    assert policy.sources()["identity:domain:create"] == "file"
    assert policy.authorize("identity:domain:create", DOMAIN_ADMIN) is True
    assert len(messages(caplog, logging.ERROR)) == 1


def test_an_atomic_replace_with_the_same_mtime_and_size_is_noticed(pfile, clock, tmp_path):
    pfile.write("identity:domain:create: 'role:admin'\n")
    assert policy.authorize("identity:domain:create", DOMAIN_ADMIN) is False
    before = os.stat(pfile.path)
    replacement = tmp_path / "policy.yaml.new"
    text = "identity:domain:create: '@'"
    replacement.write_text(text.ljust(before.st_size - 1) + "\n", encoding="utf-8")  # same length, other content
    assert replacement.stat().st_size == before.st_size
    os.utime(replacement, ns=(before.st_mtime_ns, before.st_mtime_ns))
    os.replace(replacement, pfile.path)
    assert os.stat(pfile.path).st_mtime_ns == before.st_mtime_ns
    clock.advance(3)
    assert policy.rules()["identity:domain:create"] == "@"


def test_file_appearing_later_is_picked_up(pfile, clock):
    assert policy.rules() == policy.DEFAULT_RULES
    pfile.write("identity:domain:create: '@'\n")
    clock.advance(3)
    assert policy.rules()["identity:domain:create"] == "@"


def test_file_vanishing_means_defaults_again_silently(pfile, clock, caplog):
    pfile.write("identity:domain:create: '@'\n")
    assert policy.rules()["identity:domain:create"] == "@"
    pfile.remove()
    clock.advance(3)
    assert policy.rules() == policy.DEFAULT_RULES
    assert set(policy.sources().values()) == {"default"}
    assert no_logs(caplog)


def test_changing_the_env_path_loads_the_other_file(pfile, tmp_path, monkeypatch):
    pfile.write("identity:domain:create: '@'\n")
    assert policy.rules()["identity:domain:create"] == "@"
    monkeypatch.setenv("CLOUDX_POLICY_FILE", str(tmp_path / "other.yaml"))
    assert policy.rules() == policy.DEFAULT_RULES


def test_authorize_unknown_action_raises_key_error(pfile):
    with pytest.raises(KeyError):
        policy.authorize("identity:nonsense:do", ADMIN)


def test_authorize_unknown_action_raises_even_if_the_file_names_it(pfile):
    pfile.write("helper: 'role:admin'\n")
    with pytest.raises(KeyError):
        policy.authorize("helper", ADMIN)


def test_authorize_reads_token_claims(pfile):
    assert policy.authorize("identity:user:create", {"role": "admin"}) is True
    assert policy.authorize("identity:user:create", {"role": "member", "domain_admin": True}) is True
    assert policy.authorize("identity:user:create", {"role": "member", "domain_admin": False}) is False
    assert policy.authorize("identity:user:create", {"role": None, "domain_admin": None}) is False
    assert policy.authorize("identity:user:create", {}) is False


def test_reset_drops_the_cache(pfile):
    pfile.write("identity:domain:create: '@'\n")
    assert policy.rules()["identity:domain:create"] == "@"
    pfile.remove()
    policy.reset()
    assert policy.rules() == policy.DEFAULT_RULES


def test_module_imports_without_environment_setup():
    import subprocess

    code = (
        "import sys; sys.path.insert(0, %r); import shared.policy"
        % os.path.join(os.path.dirname(__file__), "..", "..", "services")
    )
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1"}
    subprocess.run([sys.executable, "-I", "-c", code], env=env, check=True)


def test_effective_returns_rules_and_sources_of_one_snapshot(pfile):
    pfile.write('identity:domain:create: "role:member"\n')

    rules, sources = policy.effective()

    assert rules == policy.rules()
    assert sources == policy.sources()
    assert rules["identity:domain:create"] == "role:member"
    assert sources["identity:domain:create"] == "file"
    rules["identity:domain:create"] = "!"  # a copy: the caller cannot change the engine
    assert policy.rules()["identity:domain:create"] == "role:member"


def test_effective_does_not_mix_two_loads(pfile, monkeypatch):
    pfile.write('identity:domain:create: "role:member"\n')
    seen = []
    real = policy._current

    def counting():
        seen.append(1)
        return real()

    monkeypatch.setattr(policy, "_current", counting)

    policy.effective()

    assert len(seen) == 1


# ── file structure: tags, anchors, aliases and duplicate keys are file errors ──

UNSAFE_FILES = [
    pytest.param("identity:domain:create: ! role:member\n", 1, id="bang-then-rule"),
    pytest.param("identity:user:delete: ! not role:member\n", 1, id="bang-then-not"),
    pytest.param("identity:domain:create: !\n", 1, id="bare-bang"),
    pytest.param('identity:domain:create: !!str "role:member"\n', 1, id="explicit-str-tag"),
    pytest.param('x: &a "@"\nidentity:domain:create: *a\n', 1, id="anchor-and-alias"),
    pytest.param("# a comment\n\nidentity:domain:create: !x role:member\n", 3, id="line-number"),
    pytest.param("identity:domain:create: @\n", 1, id="bare-at-is-invalid-yaml"),
]


@pytest.mark.parametrize("text, line", UNSAFE_FILES)
def test_a_tag_anchor_or_alias_makes_the_whole_file_invalid(pfile, caplog, text, line):
    pfile.write(text)
    assert policy.rules() == policy.DEFAULT_RULES
    assert policy.authorize("identity:domain:create", ADMIN) is True
    assert policy.authorize("identity:domain:create", MEMBER) is False
    assert policy.authorize("identity:user:delete", MEMBER) is False
    assert "file" not in policy.sources().values()
    logged = messages(caplog, logging.ERROR)
    assert len(logged) == 1
    problems = policy.check_file(str(pfile.path))
    assert problems == logged  # one validation path: the same line in the log and in `policy check`
    assert f"line {line}" in problems[0]


def test_the_tag_message_says_to_quote_the_rule(pfile):
    pfile.write("a: 1\nidentity:domain:create: ! role:member\n")
    assert policy.check_file(str(pfile.path)) == [
        'policy file: line 2: tags, anchors and aliases are not allowed; '
        'put the rule in double quotes ("!" or "@" included)'
    ]


def test_a_tagged_reload_keeps_the_previous_valid_override(pfile, clock, caplog):
    pfile.write('identity:domain:create: "role:admin or role:member"\n')
    assert policy.authorize("identity:domain:create", MEMBER) is True
    pfile.write("identity:domain:create: ! role:member\n")
    clock.advance(3)
    assert policy.authorize("identity:domain:create", MEMBER) is True
    assert policy.authorize("identity:domain:create", ADMIN) is True
    assert policy.sources()["identity:domain:create"] == "file"
    assert len(messages(caplog, logging.ERROR)) == 1


def test_quoted_bang_and_at_still_work(pfile, caplog):
    pfile.write('identity:domain:create: "!"\nidentity:user:list: "@"\n')
    assert policy.authorize("identity:domain:create", ADMIN) is False
    assert policy.authorize("identity:user:list", MEMBER) is True
    assert policy.sources()["identity:domain:create"] == "file"
    assert policy.check_file(str(pfile.path)) == []
    assert no_logs(caplog)


DUPLICATE_FILES = [
    pytest.param('identity:domain:create: "!"\nidentity:domain:create: "role:admin"\n',
                 "'identity:domain:create'", 2, 1, id="action"),
    pytest.param('r: "role:admin"\nidentity:user:list: "rule:r"\nr: "role:member"\n',
                 "'r'", 3, 1, id="named-rule"),
    pytest.param('"identity:domain:create": "!"\nidentity:domain:create: "role:admin"\n',
                 "'identity:domain:create'", 2, 1, id="differs-only-in-quoting"),
    pytest.param('"a\\nb": "role:admin"\n"a\\nb": "role:member"\n', "'a\\nb'", 2, 1, id="key-with-newline"),
]


@pytest.mark.parametrize("text, name, line, first", DUPLICATE_FILES)
def test_a_duplicate_entry_makes_the_whole_file_invalid(pfile, caplog, text, name, line, first):
    pfile.write(text)
    assert policy.rules() == policy.DEFAULT_RULES
    assert policy.authorize("identity:domain:create", MEMBER) is False
    assert "file" not in policy.sources().values()
    logged = messages(caplog, logging.ERROR)
    assert logged == [f"policy file: line {line}: duplicate entry {name} (first at line {first})"]
    assert policy.check_file(str(pfile.path)) == logged
    assert len(logged[0].splitlines()) == 1


def test_a_duplicate_entry_on_reload_keeps_the_last_good_rules(pfile, clock, caplog):
    pfile.write('identity:domain:create: "@"\n')
    assert policy.authorize("identity:domain:create", MEMBER) is True
    pfile.write('identity:domain:create: "!"\nidentity:domain:create: "role:admin"\n')
    clock.advance(3)
    assert policy.authorize("identity:domain:create", MEMBER) is True
    assert len(messages(caplog, logging.ERROR)) == 1


@pytest.mark.parametrize("text, line", [
    ('<<: {identity:domain:create: "@"}\nidentity:domain:create: "role:admin"\n', 1),
    ('identity:domain:create: "role:admin"\n<<: {identity:user:list: "@"}\n', 2),
    ('<<: [{identity:user:list: "@"}]\n', 1),
    ('# a comment\n<<: {}\n', 2),
])
def test_a_merge_key_is_refused_as_a_file_error(pfile, caplog, text, line):
    pfile.write(text)

    assert policy.authorize("identity:user:list", MEMBER) is False  # the defaults apply
    assert policy.authorize("identity:domain:create", MEMBER) is False
    assert policy.authorize("identity:domain:create", ADMIN) is True
    assert set(policy.sources().values()) == {"default"}
    expected = f"policy file: line {line}: merge keys (<<) are not allowed; write every entry out"
    assert messages(caplog, logging.ERROR) == [expected]
    assert policy.check_file(str(pfile.path)) == [expected]


def test_a_merge_key_on_reload_keeps_the_last_good_rules(pfile, clock, caplog):
    pfile.write('identity:domain:create: "@"\n')
    assert policy.authorize("identity:domain:create", MEMBER) is True
    pfile.write('<<: {identity:domain:create: "!"}\nidentity:domain:create: "role:admin"\n')
    clock.advance(3)
    assert policy.authorize("identity:domain:create", MEMBER) is True  # the previous valid override stays
    assert len(messages(caplog, logging.ERROR)) == 1


def test_a_quoted_merge_marker_is_an_ordinary_name(pfile):
    pfile.write('"<<": "@"\nidentity:user:list: "rule:<<"\n')
    assert policy.check_file(str(pfile.path)) == []
    assert policy.authorize("identity:user:list", MEMBER) is True  # a named rule called "<<", used by an action


# ── evaluation, log lines and file types ───────────────────────────────────────

def test_evaluation_is_linear_in_the_number_of_rules(pfile, monkeypatch):
    lines = ['r0: "role:nobody"']
    for i in range(1, 7):
        lines.append(f'r{i}: "' + " or ".join([f"rule:r{i - 1}"] * 20) + '"')
    lines.append('identity:user:list: "rule:r6 or role:admin"')
    pfile.write("\n".join(lines) + "\n")
    assert policy.check_file(str(pfile.path)) == []
    calls = []
    real = policy.evaluate

    def counting(expr, token, resolve):
        calls.append(expr)
        return real(expr, token, resolve)

    monkeypatch.setattr(policy, "evaluate", counting)
    assert policy.authorize("identity:user:list", MEMBER) is False
    assert len(calls) == 1 + 7  # the action itself, then each of r0..r6 once


def test_a_non_string_value_is_never_echoed(pfile, caplog):
    pfile.write("identity:user:list: [" + ",".join(["1"] * 100000) + "]\n")
    assert policy.rules() == policy.DEFAULT_RULES
    logged = messages(caplog, logging.ERROR)
    assert len(logged) == 1 and len(logged[0]) < 300
    assert "list" in logged[0] and "not a string" in logged[0]
    assert policy.check_file(str(pfile.path)) == logged


def test_a_name_with_a_newline_cannot_forge_a_log_line(pfile, caplog):
    pfile.write('"evil\\nERROR forged": [1]\n"typo\\nWARNING forged": "role:admin"\n')
    policy.rules()
    for line in policy.check_file(str(pfile.path)):
        assert "\n" not in line
    assert all("\n" not in m for m in messages(caplog, logging.ERROR) + messages(caplog, logging.WARNING))
    assert any("evil\\nERROR forged" in m for m in messages(caplog, logging.ERROR))
    assert any("typo\\nWARNING forged" in m for m in messages(caplog, logging.WARNING))


def test_a_directory_at_the_path_is_a_file_error(pfile, caplog):
    pfile.path.mkdir()
    assert policy.rules() == policy.DEFAULT_RULES
    logged = messages(caplog, logging.ERROR)
    assert len(logged) == 1 and "not a regular file" in logged[0]
    assert policy.check_file(str(pfile.path)) == logged


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no named pipes here")
def test_a_fifo_at_the_path_is_refused_without_blocking(pfile, caplog):
    import threading

    os.mkfifo(pfile.path)
    result = []
    worker = threading.Thread(target=lambda: result.append(policy.rules()), daemon=True)
    worker.start()
    worker.join(5)
    if worker.is_alive():  # a regression would block in open(); release it so the suite carries on
        fd = os.open(pfile.path, os.O_WRONLY | os.O_NONBLOCK)
        os.close(fd)
        worker.join(5)
        pytest.fail("loading a FIFO blocked")
    assert result == [policy.DEFAULT_RULES]
    logged = messages(caplog, logging.ERROR)
    assert len(logged) == 1 and "not a regular file" in logged[0]
    assert policy.check_file(str(pfile.path)) == logged


def test_a_file_over_1_mib_is_refused_without_parsing(pfile, caplog, monkeypatch):
    assert policy.MAX_FILE_BYTES == 1024 * 1024
    pfile.write("#" * (policy.MAX_FILE_BYTES + 1))

    def no_parse(*args, **kwargs):
        raise AssertionError("the file must not be parsed")

    monkeypatch.setattr(policy, "_StrictLoader", no_parse)
    assert policy.rules() == policy.DEFAULT_RULES
    logged = messages(caplog, logging.ERROR)
    assert len(logged) == 1 and "too large" in logged[0] and str(policy.MAX_FILE_BYTES + 1) in logged[0]
    assert policy.check_file(str(pfile.path)) == logged


def test_a_file_of_exactly_1_mib_is_read(pfile, caplog):
    head = 'identity:user:list: "role:admin"\n'
    pfile.write(head + "#" * (policy.MAX_FILE_BYTES - len(head)))
    assert pfile.path.stat().st_size == policy.MAX_FILE_BYTES
    assert policy.sources()["identity:user:list"] == "file"
    assert no_logs(caplog)
