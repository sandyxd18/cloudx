# Policy file: operator-editable rules for admin-only actions

Date: 2026-11-04 · Delivery: two PRs · Base: main after #105-#109

## Goal

An operator can change who may perform an admin-only API action by editing one file, `/etc/cloudx/policy.yaml`, without changing code and without restarting the service. Without the file, every decision is exactly what it is today.

## Decisions taken (with the project owner)

- **Scope: admin-only actions.** The "only admin" and "admin or domain admin" gates become named actions whose rule comes from the file. Ownership and visibility rules (a user sees the resources of their own project, "admin or owner", per-domain lists, quotas of one's own project) stay in code. They decide *what data* a caller gets, not *whether the action is allowed*.
- **Two PRs.** PR 1: the engine, the file, the validation command, the parity tests, the conversion of auth-api and the documentation. PR 2: the same pattern in every other service. Until PR 2 is merged the file applies to auth-api only, and the documentation says so.
- **No `oslo.policy` package.** It pulls `oslo.config` and more into the `.deb` pin set. A small parser for a subset of its syntax lives in `services/shared/policy.py`.
- **No hidden exceptions.** A rule is what it says. An operator who writes `!` for an action closes it for admins too. `cloudx-manage policy check` and the documentation are the guard rails.

## PR 1

### The file

`/etc/cloudx/policy.yaml`: a YAML mapping `action: rule` where both are strings. Only the actions that the operator wants to change need to be listed; anything not listed keeps the built-in rule. The file is owned by `root:cloudx`, mode 640 (like `cloudx.conf`, so the service user can read it). It must exist on every node that runs the service whose actions it changes.

### Rule syntax

A rule is a boolean expression:

| Element | Meaning |
|---|---|
| `role:NAME` | true when the token's `role` claim equals NAME (the same claim the code checks today) |
| `domain_admin:True` / `domain_admin:False` | true when the token's `domain_admin` claim is truthy / falsy |
| `rule:NAME` | the result of another rule of the same file or table (for example a shared `admin_required`) |
| `@` | always true |
| `!` | always false |
| `and`, `or`, `not`, `( )` | combination; `not` binds tightest, then `and`, then `or` |

Nothing else is accepted (no `project_id:%(project_id)s`, no `is_admin`, no `http:` checks). A rule that uses anything else is a rule error (see below). In the YAML file a rule that starts with `!` or `@`, or that contains `: `, must be quoted (`"!"`, `"@"`); unquoted, YAML itself reads them as a tag, a reserved indicator or a mapping, which is a file error or a rule error as described below. A `rule:` reference to an unknown name or a reference cycle is a rule error. References nest at most 10 deep.

The evaluation context is the decoded token. A token for the internal service call (`X-Internal-Token`) is the payload that `require_user_or_internal` already builds (`role: "admin"`), so internal calls follow the rule of `role:admin`.

### Loading and failure modes

- The file is read when the engine is first used and again when its `mtime` changes; the `mtime` is checked at most once every 2 seconds.
- A rule error: that single action keeps its built-in rule, and an ERROR is logged with the action name and the reason. Other actions of the file still apply.
- A file that cannot be read or is not valid YAML, or whose top level is not a mapping: every action keeps its built-in rule and an ERROR is logged. The last good rules are kept when a reload fails.
- An entry in the file whose name is not an action of the table is a named rule: other rules may use it with `rule:NAME`. If no rule refers to it, a WARNING says it is neither an action nor a used rule (a mistyped action must not pass silently).
- No file: the built-in table, no log noise.
- A service never fails to start because of the policy file.

### Enforcement

`shared.policy.require(action: str)` returns a FastAPI dependency. It authenticates like `CurrentUser`, evaluates the rule of `action` against the token, returns the token payload when allowed, and answers `403` with `{"detail": "Policy does not allow <action>"}` when not. An action that is not in the table is a programming error: `require` raises `KeyError` at import time of the router, so a misspelt action fails the test suite, not a request.

`AdminUser` and `DomainAdminUser` stay exported for the services that PR 2 converts.

### The 22 actions of auth-api

Built-in rule `role:admin or domain_admin:True` (replaces `DomainAdminUser`):

`identity:user:create`, `identity:user:list`, `identity:user:delete`, `identity:domain:list`, `identity:domain:show`, `identity:domain:quota-show`, `identity:project:create`, `identity:project:update`, `identity:project:delete`, `identity:role-assignment:create`, `identity:role-assignment:delete`

Built-in rule `role:admin` (replaces `AdminUser`):

`identity:domain:create`, `identity:domain:update`, `identity:domain:delete`, `identity:domain:quota-set`, `identity:domain:quota-clear`, `identity:service:create`, `identity:service:delete`, `identity:endpoint:create`, `identity:endpoint:delete`, `identity:project:quota-set`, `identity:audit-log:list`

The table lives in `services/shared/policy.py` (one dict, action to rule string). The 17 names that the table holds today are removed: nothing enforces them, they only appear in `GET /policy`.

Checked and left in code, because they decide what data a caller gets and not whether the action is allowed: the "owner or admin" checks of application credentials and access keys; the list scoping of role assignments, projects, groups and trusts; `switch-project`; `show_project`; `create_audit_log` (a caller writes under their own identity unless admin); the trust checks; `UserOrInternal` on `list_projects` and `get_project_quota`.

### Endpoints and commands

- `GET /policy` keeps its key `policy` (action to rule string, now the effective rules) and gains `sources` (action to `"default"` or `"file"`).
- `POST /policy/enforce` keeps its body and answer; it now uses the engine, and an unknown `action` answers 400.
- `cloudx-manage policy check [FILE]` (default `/etc/cloudx/policy.yaml`) loads the file and prints one line per problem; exit status 0 when there is none, 1 otherwise. It also lists the actions whose rule differs from the built-in one.
- `cloudx-manage policy defaults` prints the built-in table as YAML with comments. `etc/cloudx/policy.yaml.sample` is that output.
- Both are subcommands in `services/shared/manage.py` (the `cloudx-manage` entry point).

### Tests

- Parser and evaluator: every element of the table above, precedence, parentheses, `rule:` references and cycles, malformed input (empty rule, dangling operator, unknown atom, unbalanced parenthesis).
- Parity: for every action of the table and every identity in {system admin, domain admin (role `member`, `domain_admin` true), member, reader, internal}, the engine's decision with the built-in rules equals the decision of the legacy gate, which the test spells out independently as two small functions (`admin only`, `admin or domain admin`).
- Routes: each converted route answers 403 for a `member` token and not 403 for an `admin` token, on the real auth-api app. Request bodies may be invalid; a dependency that raises is answered before body validation.
- File effects, on the real app: a rule `role:member` for `identity:user:create` lets a member create a user (201); `!` for `identity:domain:create` makes an admin get 403; a rule error keeps that action's built-in rule and logs; an entry that is neither an action nor referenced logs a WARNING; a named rule can be referenced with `rule:NAME`; an unreadable file keeps the built-ins; a changed `mtime` is picked up without restart; no file changes nothing.
- `policy check` and `policy defaults`: output and exit status; the sample file equals the output of `policy defaults` (no drift).

### Documentation and packaging

- New `docs/policy.md`: file location and permissions, syntax, the 22 actions with their built-in rules, validation, the failure modes, the limits (auth-api only until PR 2; ownership and visibility stay in code; the file must exist on every node of the service; an operator can lock admins out with `!`).
- `docs/feature-comparison.md`: the oslo.policy row says what exists; `CHANGELOG.md`: Added.
- The sample ships like the config sample: `/usr/share/cloudx/policy.yaml.sample` in `cloudx-common` (see `CONF_SAMPLE` in `scripts/cloudx_deb/build.py`); the real `/etc/cloudx/policy.yaml` is never created or overwritten by a package.

## PR 2 (outline, designed after PR 1 is merged)

The same pattern for the other services: about 45 further dependency-form gates (`AdminUser` 56 and `require_admin` 8 and `AdminOrInternal` 3 in total, minus the ones in auth-api and `shared`) and the inline gates that turn out to be pure gates when each site is read. Each service's actions join the one table; its sites get the same parity and route tests. Inline checks that mix a role with ownership stay in code. Its spec is written after PR 1, with the real counts.

## Out of scope

Ownership and visibility rules in the file; per-project policy; `oslo.policy` itself; a policy API to edit rules at runtime; policy for the dashboard's own pages.

## Risks

- A rule file on one node and not on another gives different answers for the same action. Documented; `policy check` runs on each node.
- Locking out admins with `!` or a typo in a role name is possible and is not prevented. `policy check` lists every rule that differs from the built-in one.
- The conversion touches 22 dependencies in auth-api; the parity and route tests are what shows that none was changed by accident.
