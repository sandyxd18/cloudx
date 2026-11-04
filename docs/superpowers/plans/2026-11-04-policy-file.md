# Policy File (engine and auth-api) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An operator edits `/etc/cloudx/policy.yaml` to change who may perform the 22 admin-only actions of auth-api, with no restart; without the file nothing changes.

**Architecture:** A pure rule parser (`shared/policy_rules.py`) and an engine (`shared/policy.py`: default table, file loading with mtime reload, `require()` dependency). auth-api swaps its `AdminUser`/`DomainAdminUser` gates for `gate("identity:...")` dependencies. `cloudx-manage policy check|defaults` validates and documents the file.

**Tech Stack:** Python, FastAPI, PyYAML (`yaml.safe_load`, already pinned), argparse (`shared/manage.py`), pytest. No `oslo.policy`.

**Spec:** `docs/superpowers/specs/2026-11-04-policy-file-design.md`

## Global Constraints

- File: `/etc/cloudx/policy.yaml`, overridable with env `CLOUDX_POLICY_FILE` (for tests and unusual layouts); mode 640 `root:cloudx`; a YAML mapping `action: rule`, both strings; read with `yaml.safe_load`.
- Syntax: `role:NAME`, `domain_admin:True|False`, `rule:NAME`, `@` (true), `!` (false), `and`, `or`, `not`, parentheses; `not` binds tightest, then `and`, then `or`. Nothing else is accepted. `rule:` references nest at most 10 deep; unknown names and cycles are rule errors. `role:NAME` compares the token's `role` claim only.
- Failure modes (a service never fails to start because of the file): a rule error keeps that action's default and logs ERROR with the action and the reason; an unreadable file, invalid YAML or a non-mapping top level keeps all defaults (the last good rules when a reload fails) and logs ERROR; an empty file is no overrides and logs nothing; no file is silent; an entry that is not an action is a named rule, and one that no rule references logs a WARNING.
- Reload when `mtime` changes, checked at most once every 2 seconds.
- Denial: `403` with `{"detail": "Policy does not allow <action>"}`. `require()` of an action that is not in the table raises `KeyError` immediately.
- Authentication comes before policy: a missing or revoked token keeps the 401 of the authenticate dependency; the internal token behaves as `role: admin`.
- `GET /policy` keeps key `policy` (effective rules) and adds `sources` (`"default"` or `"file"`); `POST /policy/enforce` keeps its body and answer, unknown `action` answers 400.
- The 17 old names of `POLICY_RULES` and the functions `enforce` and `list_rules` are removed.
- Ownership and visibility checks stay in code; only the 22 gates listed in Task 2 move. Docs and comments in English. No version bump.
- Commit trailers on every commit: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_015ZZ3VowHusnEAJzUhJtVob`. Run tests with `.venv-dev/bin/python -m pytest -p no:cacheprovider`.

## Review Focus

1. An empty policy file (`touch policy.yaml`, a YAML `null`) must mean "no overrides", not a crash or a lock-out. Test in Task 2.
2. Unquoted `!` and `@` in the YAML (`identity:domain:create: !`) must never open or close an action silently: the result is a logged error and the defaults. Test in Task 2; documented in Task 7.
3. A file replaced by an invalid one while the service runs must keep the last good rules. Test in Task 2.
4. A token with missing claims (no `role`, no `domain_admin`) evaluates every atom false and never raises. Test in Task 1.
5. A missing or revoked token on a policy-gated route still answers 401, and `require()` never hides the authenticate dependency's error. Test in Task 3.

---

### Task 1: Rule parser and evaluator

**Files:**
- Create: `services/shared/policy_rules.py`
- Test: `tests/shared/test_policy_rules.py`

**Interfaces:**
- Produces: `class RuleError(ValueError)`; `parse(text: str) -> Expr` (an opaque tree; raises `RuleError` for an empty rule, an unknown atom, a dangling operator or an unbalanced parenthesis); `references(expr: Expr) -> set[str]` (the `NAME`s of every `rule:NAME`); `evaluate(expr: Expr, token: dict, resolve: Callable[[str], bool]) -> bool` (`resolve(name)` evaluates a referenced rule).

- [ ] **Step 1: Write failing tests** in `tests/shared/test_policy_rules.py`: `test_atoms` (`role:admin` true for `{"role": "admin"}` and false for member; `domain_admin:True` true for `{"domain_admin": True}`, `domain_admin:False` true when the claim is absent or falsy; `@` true; `!` false), `test_precedence` (`role:a or role:b and role:c` equals `a or (b and c)`; `not role:a and role:b` equals `(not a) and b`), `test_parentheses`, `test_rule_reference_calls_resolve` and `test_references_lists_names`, `test_missing_claims_are_false_and_never_raise` (token `{}` and `{"role": None}` evaluate `role:admin`, `domain_admin:True`, `not role:admin` without error — Review Focus 4), `test_operators_are_case_sensitive_lowercase` (`AND` is a `RuleError`), and a parametrized `test_malformed_rules_raise_rule_error` with `""`, `"   "`, `"role:"`, `"role:admin and"`, `"(role:admin"`, `"role:admin)"`, `"foo:bar"`, `"role:admin role:member"`, `"and"`.
- [ ] **Step 2: Run** `tests/shared/test_policy_rules.py`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement** the module: a small tokenizer (identifiers like `role:admin` end at whitespace or a parenthesis; `@`, `!`, `(`, `)`, `and`, `or`, `not`) and a recursive-descent parser (or > and > not > atom). `domain_admin` accepts exactly `True` and `False` as values.
- [ ] **Step 4: Run** the file. Expected: PASS.
- [ ] **Step 5: Commit** `feat(policy): rule parser and evaluator`.

### Task 2: The engine: default table, file loading, reload

**Files:**
- Modify: `services/shared/policy.py` (rewrite; remove `POLICY_RULES`, `_get_rule`, `enforce`, `list_rules`)
- Test: `tests/shared/test_policy.py`, `tests/shared/test_policy_parity.py`

**Interfaces:**
- Consumes: Task 1's `parse`, `references`, `evaluate`, `RuleError`.
- Produces in `shared/policy.py`: `POLICY_FILE() -> str` (env `CLOUDX_POLICY_FILE`, default `/etc/cloudx/policy.yaml`, read at call time); `DEFAULT_RULES: dict[str, str]` (the 22 actions below); `rules() -> dict[str, str]` (effective rules for the actions, reloading when due); `sources() -> dict[str, str]` (`"default"` or `"file"` per action); `authorize(action: str, token: dict) -> bool` (`KeyError` for an action not in `DEFAULT_RULES`); `reset() -> None` (drops the cache; for tests); a module-level `_monotonic = time.monotonic` that tests may patch.
- The 22 defaults. `role:admin or domain_admin:True`: `identity:user:create`, `identity:user:list`, `identity:user:delete`, `identity:domain:list`, `identity:domain:show`, `identity:domain:quota-show`, `identity:project:create`, `identity:project:update`, `identity:project:delete`, `identity:role-assignment:create`, `identity:role-assignment:delete`. `role:admin`: `identity:domain:create`, `identity:domain:update`, `identity:domain:delete`, `identity:domain:quota-set`, `identity:domain:quota-clear`, `identity:service:create`, `identity:service:delete`, `identity:endpoint:create`, `identity:endpoint:delete`, `identity:project:quota-set`, `identity:audit-log:list`.

- [ ] **Step 1: Write failing tests** in `tests/shared/test_policy.py` (set `CLOUDX_POLICY_FILE` to a `tmp_path` file, call `policy.reset()` in a fixture, use `caplog`): `test_defaults_are_the_22_actions_of_the_spec`; `test_no_file_gives_defaults_and_logs_nothing`; `test_file_overrides_one_action_only` (and `sources()` shows `file` for it); `test_empty_file_means_no_overrides_and_logs_nothing` and `test_null_document_means_no_overrides` (Review Focus 1); `test_top_level_that_is_not_a_mapping_keeps_defaults_and_logs_error` (a list, a string); `test_non_string_rule_is_a_rule_error_for_that_action` (`identity:user:create: true`); `test_rule_error_keeps_only_that_actions_default` (`role:admin and` for one action, another action still overridden; ERROR names the action); `test_unknown_reference_and_cycle_are_rule_errors` (`rule:nope`, `a: "rule:b"`/`b: "rule:a"`, and a chain of 11 references); `test_named_rule_can_be_referenced` (`admin_required: "role:admin"`, `identity:user:create: "rule:admin_required"`); `test_unreferenced_unknown_entry_logs_warning` (a mistyped `identity:user:creat`); `test_unquoted_special_characters_never_change_the_decision` parametrized over file texts `identity:domain:create: !` and `identity:domain:create: @` (Review Focus 2: an admin token is still allowed, the other actions keep their defaults, ERROR logged); `test_invalid_yaml_keeps_defaults_and_logs_error`; `test_a_failed_reload_keeps_the_last_good_rules` (valid override loaded, file overwritten with invalid YAML, patched `_monotonic` advanced 3 s: the override still applies; Review Focus 3); `test_mtime_change_is_picked_up_without_restart` (patched clock; also `test_mtime_is_checked_at_most_every_2_seconds`: a change inside 2 s is not seen yet); `test_authorize_unknown_action_raises_key_error`; `test_authorize_reads_token_claims`. In `tests/shared/test_policy_parity.py`: the identities `system admin {"role": "admin"}`, `domain admin {"role": "member", "domain_admin": True}`, `member {"role": "member"}`, `reader {"role": "reader"}`, `internal` (the payload `shared.auth_middleware.require_user_or_internal` builds for the internal token, copied into the test), and two independent legacy functions in the test (`legacy_admin_only(t): t.get("role") == "admin"`, `legacy_admin_or_domain_admin(t): t.get("role") == "admin" or bool(t.get("domain_admin"))`, mirroring `_require_admin` and `_require_domain_admin` in `services/auth/app/deps.py`); `test_default_rules_decide_like_the_legacy_gate` asserts, for each of the 22 actions and each identity, `authorize(action, identity)` equals the legacy function of the action's group (the two groups listed as literal sets in the test).
- [ ] **Step 2: Run** both files. Expected: FAIL (the old module has no such API).
- [ ] **Step 3: Implement** `shared/policy.py` to the interface: parse every default once; load the file with `yaml.safe_load`; per-entry validation and the failure modes of Global Constraints; resolve `rule:` through a function that looks in the effective rules (file entries first, then defaults), with a depth counter for the limit of 10; cache `(mtime, rules, sources)` and the time of the last check. Log with `logging.getLogger("cloudx-policy")`.
- [ ] **Step 4: Run** both files. Expected: PASS. Only `services/auth/app/routers/policy.py` imports the old API (`grep -rn "shared.policy" services tests`), so keep `enforce(action, token_payload, target_project_id=None, raise_on_deny=True)` and `list_rules()` as thin wrappers over the new functions until Task 5 rewrites that router and deletes them; `tests/auth -q` must still pass.
- [ ] **Step 5: Commit** `feat(policy): default table, policy file loading and reload`.

### Task 3: The `require()` dependency

**Files:**
- Modify: `services/shared/policy.py`
- Test: `tests/shared/test_policy_require.py`

**Interfaces:**
- Consumes: Task 2's `authorize`, `DEFAULT_RULES`.
- Produces: `require(action: str, authenticate: Callable | None = None) -> Callable` (a FastAPI dependency: it depends on `authenticate`, which defaults to `shared.auth_middleware.require_auth` imported lazily so that importing `shared.policy` needs no `AUTH_SECRET_KEY`; it returns the token payload when allowed); `required_actions() -> set[str]` (every action passed to `require` in this process).

- [ ] **Step 1: Write failing tests** in `tests/shared/test_policy_require.py` with a tiny FastAPI app and a fake `authenticate` dependency: `test_allowed_returns_the_payload`; `test_denied_answers_403_with_the_action_in_the_detail` (body exactly `{"detail": "Policy does not allow identity:user:create"}`); `test_authentication_error_is_not_hidden` (the fake authenticate raises `HTTPException(401)`: the response is 401 — Review Focus 5); `test_unknown_action_raises_key_error_immediately`; `test_required_actions_records_the_actions`; `test_default_authenticate_is_the_shared_require_auth` (set `AUTH_SECRET_KEY`, sign a token like `tests/billing/test_domain_scoped_token.py` does, call a route: admin 200, member 403, no token 401); `test_file_change_applies_to_the_next_request` (file says `role:member`, member allowed, without rebuilding the app).
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement** `require` and `required_actions` (a module-level set) in `shared/policy.py`.
- [ ] **Step 4: Run** the file plus `tests/shared -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(policy): require() dependency`.

### Task 4: Convert auth-api, group A: users, projects, role assignments

**Files:**
- Modify: `services/auth/app/deps.py` (add `gate`), `services/auth/app/routers/users.py`, `projects.py`, `role_assignments.py`
- Test: `tests/auth/test_policy_routes.py`

**Interfaces:**
- Consumes: Task 3's `require`.
- Produces: `deps.gate(action: str) -> params.Depends` returning `Depends(policy.require(action, _require_token))` (so auth-api keeps its own token checks: revocation, reader rule, trust-token confinement). Pattern for every converted route: add `dependencies=[gate("...")]` to the route decorator; replace a `_: AdminUser` parameter by nothing and a `current: DomainAdminUser`/`AdminUser` parameter that the handler uses by `current: CurrentUser`. Behavior must not change otherwise. `AdminUser` and `DomainAdminUser` stay defined in `deps.py`.
- Routes of this group: `identity:user:create` (create_user), `identity:user:list` (list_users), `identity:user:delete` (delete_user), `identity:project:create`, `identity:project:update`, `identity:project:delete`, `identity:project:quota-set` (update_project_quota), `identity:role-assignment:create` (assign_role), `identity:role-assignment:delete` (unassign_role).

- [ ] **Step 1: Write failing tests** in `tests/auth/test_policy_routes.py` using the fixture style of `tests/auth/test_app_credential_roles.py` (domain, project, users `adm` admin, `da` member with `is_domain_admin`, `u1` member): a module-level `ROUTES` list of `(method, path, action)` holding the 9 routes of this group (paths with ids that need not exist); `test_member_is_denied_with_the_policy_detail` (parametrized: 403 and `Policy does not allow <action>`), `test_admin_is_not_denied` (status != 403, request bodies may be invalid), `test_domain_admin_matches_the_old_gate` (allowed on the 8 `DomainAdminUser` routes of the group, denied on `identity:project:quota-set`), `test_policy_file_can_open_an_action` (file `identity:user:create: "role:member"`: a member `POST /users` is not 403; same member still 403 on `identity:user:delete`), `test_policy_file_can_close_an_action_for_admins` (the file line `identity:project:create: "!"`: an admin gets 403), `test_revoked_token_is_still_401` (log out, reuse the token on a converted route: 401).
- [ ] **Step 2: Run.** Expected: FAIL (members are denied with the old detail `admin or domain admin required`, files have no effect).
- [ ] **Step 3: Implement** `gate` and convert the 9 routes.
- [ ] **Step 4: Run** `tests/auth -q`. Expected: PASS (existing auth tests unchanged; if one asserts the old 403 detail text, update that assertion to the new text and say so in the commit message).
- [ ] **Step 5: Commit** `feat(auth): policy gates for users, projects and role assignments`.

### Task 5: Convert auth-api, group B, and the /policy endpoints

**Files:**
- Modify: `services/auth/app/routers/domains.py`, `catalog.py`, `audit.py`, `policy.py`; `services/shared/policy.py` (remove the temporary `enforce`/`list_rules` wrappers)
- Test: `tests/auth/test_policy_routes.py` (extend `ROUTES`), `tests/auth/test_policy_endpoints.py`

**Interfaces:**
- Consumes: Task 4's `gate`.
- Routes of this group: `identity:domain:list`, `identity:domain:show`, `identity:domain:create`, `identity:domain:update`, `identity:domain:delete`, `identity:domain:quota-show` (get_domain_quota), `identity:domain:quota-set`, `identity:domain:quota-clear`, `identity:service:create`, `identity:service:delete`, `identity:endpoint:create`, `identity:endpoint:delete`, `identity:audit-log:list` (the `GET /audit-logs` route only).

- [ ] **Step 1: Write failing tests**: append the 13 routes to `ROUTES` (the parametrized tests of Task 4 cover them); `test_every_default_action_is_used_by_a_route` in `tests/auth/test_policy_routes.py` (import the auth app, then `policy.required_actions() >= set(policy.DEFAULT_RULES)`); in `tests/auth/test_policy_endpoints.py`: `test_get_policy_lists_effective_rules_and_sources` (key `policy` maps action to rule string for all 22, `sources` is `default`, and `file` for an overridden action), `test_enforce_uses_the_engine` (member vs admin for `identity:user:create`, `allowed` and `action` keys as today), `test_enforce_unknown_action_is_400`, `test_enforce_requires_a_token` (401).
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement**: convert the 13 routes with the pattern of Task 4; rewrite `routers/policy.py` on `shared.policy.rules()`, `sources()` and `authorize()`; delete the wrappers. `grep -rn "policy" services/dashboard` first: if the dashboard reads `GET /policy`, check it only uses the `policy` key.
- [ ] **Step 4: Run** `tests/auth tests/shared -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(auth): policy gates for domains, catalog and audit, new /policy answers`.

### Task 6: `cloudx-manage policy` and the sample file

**Files:**
- Modify: `services/shared/policy.py` (helpers), `services/shared/manage.py`
- Create: `etc/cloudx/policy.yaml.sample`
- Test: `tests/shared/test_policy_cli.py`

**Interfaces:**
- Consumes: Task 2's engine.
- Produces in `shared/policy.py`: `check_file(path: str | None = None) -> list[str]` (one human-readable line per problem: invalid YAML, non-mapping top level, rule error per action, entry that is neither an action nor referenced; `[]` when none; a missing file is not a problem); `differing(path: str | None = None) -> list[str]` (actions whose rule in the file differs from the default); `defaults_yaml() -> str` (every default as a commented line `# action: "rule"` under a header that explains the format and the quoting of `"!"` and `"@"`). In `manage.py`: `policy check [FILE]` (prints the problems, then `differs from the default: <action>: <rule>` lines; exit 1 when there are problems, else 0) and `policy defaults` (prints `defaults_yaml()`). `etc/cloudx/policy.yaml.sample` is `defaults_yaml()` verbatim.

- [ ] **Step 1: Write failing tests** in `tests/shared/test_policy_cli.py`: `test_check_reports_each_problem` (one file with a rule error and a mistyped entry that nothing references, one separate file with an unquoted `@` value that is invalid YAML), `test_check_ok_exit_status_and_differing_list` (valid file with one override: exit 0 and the override listed), `test_check_missing_file_is_ok`, `test_check_exit_status_is_1_with_problems`, `test_defaults_prints_every_action_commented`, `test_uncommenting_the_defaults_gives_exactly_the_table` (strip the leading `# ` of the action lines, `yaml.safe_load`, equals `DEFAULT_RULES`), `test_sample_file_equals_defaults_output` (the drift test), `test_the_sample_as_installed_is_a_no_op` (every line is a comment, `check_file` of the sample is `[]`, `rules()` equals the defaults).
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement** the three helpers and the `policy` group in `manage.py` (argparse, same style as `catalog`), and generate the sample from `defaults_yaml()`.
- [ ] **Step 4: Run** `tests/shared -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(policy): cloudx-manage policy check and defaults, sample file`.

### Task 7: Packaging, documentation, changelog

**Files:**
- Modify: `scripts/cloudx_deb/build.py` (stage the sample next to `CONF_SAMPLE`: `/usr/share/cloudx/policy.yaml.sample`), `tests/packaging/test_build.py`, `docs/README.md`, `docs/feature-comparison.md`, `CHANGELOG.md`
- Create: `docs/policy.md`

- [ ] **Step 1: Write the failing test** in `tests/packaging/test_build.py` next to the existing `/usr/share/cloudx/cloudx.conf.sample` assertion: `/usr/share/cloudx/policy.yaml.sample` is in `cloudx-common`'s files, and the package creates no `/etc/cloudx/policy.yaml`.
- [ ] **Step 2: Run** it. Expected: FAIL. **Step 3:** stage the sample; run `tests/packaging -q`, update golden files only as that suite's messages direct. Expected: PASS.
- [ ] **Step 4: Write `docs/policy.md`** from the spec: location, mode, the sample, syntax table, the 22 actions with defaults, the quoting rule for `"!"` and `"@"` with a worked example (let a domain admin create flavors is for PR 2; here: let a `member` list users, and close audit logs for everyone), validation with `cloudx-manage policy check` and `defaults`, the failure modes, and the limits (auth-api only until the next PR; ownership and visibility stay in code; the file must exist on every node that runs auth-api; `!` can lock admins out). Link it from `docs/README.md`; change the oslo.policy row of `docs/feature-comparison.md` to say what exists (and keep its table checks green); add a `CHANGELOG.md` entry under Unreleased/Added. Every `cloudx-manage` command written in the docs must exist (`scripts/check_doc_commands.py`).
- [ ] **Step 5: Run** `.venv-dev/bin/python -m pytest tests/docs tests/packaging tests/scripts -q -p no:cacheprovider`, `.venv-dev/bin/python scripts/check_feature_comparison.py`, `.venv-dev/bin/python scripts/check_doc_commands.py`. Expected: all pass.
- [ ] **Step 6: Commit** `docs: policy file (packaging, guide, changelog)`.

### Task 8: Whole-suite check

- [ ] **Step 1: Run** `.venv-dev/bin/python -m pytest -q -p no:cacheprovider` from the repo root. Expected: everything passes (report the counts; the baseline on main is 1637 passed, 65 skipped).
- [ ] **Step 2: Run** the policy tests three times in a row (`tests/shared/test_policy*.py tests/auth/test_policy_*.py`) to catch timing flakes from the mtime and clock tests. Expected: PASS each time.
