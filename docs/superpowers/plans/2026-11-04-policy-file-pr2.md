# Policy file PR 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put the 35 pure admin gates of compute, block-store, placement, billing, networking, object-store, telemetry and ha under `/etc/cloudx/policy.yaml`, with built-in rules identical to today's gates.

**Architecture:** The PR 1 engine (`services/shared/policy.py`) gets 35 more names in its table, an inline `check()` and an `.action` marker on the dependencies it builds. Each gated route keeps its parameter (same name, same position) and only changes the annotation from `AdminUser`/`AdminOrInternal` to `Annotated[dict, Depends(policy.require(ACTION))]`. A shared test helper reads the action of every route from the app, so each service's mapping is pinned by one test.

**Tech Stack:** Python, FastAPI, pytest, ruff, PyYAML (already in the engine).

**Spec:** `docs/superpowers/specs/2026-11-04-policy-file-pr2-design.md` (extends `2026-11-04-policy-file-design.md`). The spec is the authority when this plan is silent or wrong.

## Global Constraints

- Every new action has the built-in rule `role:admin`. The table then holds 57 actions: 22 `identity:*` (unchanged) plus the 35 below.
- Action names are `service:resource:verb`, kebab-case, exactly as in the spec table. PR 1's `identity:*` names do not change.
- A converted parameter keeps its **name and position**; only its annotation changes. Never move a gate into `dependencies=[...]` and delete the parameter.
- `compute:service:update` and `compute:server:evacuate` use `policy.require(action, authenticate=require_user_or_internal)` (from `shared.auth_middleware`) so the internal token (HA monitor) and the test override of `require_auth` keep working.
- The two block-store sub-actions call `policy.check(action, current)` at the place of today's test, after `require_volume(...)`.
- The 403 body of every converted gate is `{"detail": "Policy does not allow <action>"}`.
- `AdminUser`, `AdminOrInternal` and the other aliases stay exported from `shared.auth_middleware`.
- Not converted: mixed sites, `InternalAuth` routes, the four bulk routes of billing/telemetry (`AdminOrInternal`, #113), `CurrentUser` routes.
- Imports follow the repo: `from shared import policy` (not `services.shared`). Run the CI lint before every push: `ruff check --select F401,F811,F821,F841 services tests` (the exact command is in `.github/workflows/`; use that one).
- Commit trailers on every commit: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_015ZZ3VowHusnEAJzUhJtVob`.

## Review Focus

1. **The admin token still passes every converted route** (the reaper logs in as admin; a wrong action or rule would lock it out): each service's route test sends an admin and expects "not 403".
2. **The internal token on `compute:service:update` and `compute:server:evacuate`** with no `require_auth` override (HA recovery): Task 2 tests it.
3. **Positional callers**: `force_detach_volume("vol1", {"role": "admin"}, db)` and `set_inventories(name, payload, None, db)` and `create_rp` -> `create_rp_internal` keep working: Tasks 3 and 4.
4. **404 before 403 for the inline sub-actions**: a member on another project's volume gets 404; on their own volume 403: Task 3.
5. **A developer's `/etc/cloudx/policy.yaml` or a leftover cache must not change any test**: Task 1's autouse fixture, with a test that proves it. Also: a domain admin (`role: member`, `domain_admin: True`) gets 403 on every converted route, because the old gates ignored `domain_admin`.

---

### Task 1: Engine, table, helper, isolation

**Files:**
- Modify: `services/shared/policy.py`
- Modify: `tests/conftest.py`
- Create: `tests/gates.py`
- Modify (update the "exactly 22" assertions): `tests/shared/test_policy.py`, `tests/shared/test_policy_parity.py`, `tests/shared/test_policy_cli.py`, `tests/auth/test_policy_endpoints.py`, `tests/auth/test_policy_routes.py`
- Test: `tests/shared/test_policy_engine_pr2.py`

**Interfaces:**
- Produces in `shared.policy`:
  - `DEFAULT_RULES: Mapping[str, str]`, a `types.MappingProxyType` of 57 entries (the 35 new ones, all `"role:admin"`, are named in the spec table).
  - `check(action: str, token: dict) -> None`: raises `HTTPException(403, f"Policy does not allow {action}")` when `authorize` is false; `KeyError` for an unknown action.
  - `require(...)` unchanged in signature; the function it returns has attribute `action` equal to the action name.
- Produces in `tests/gates.py`:
  - `route_actions(app) -> dict[tuple[str, str], str]`: for every `APIRoute` of `app`, for every method in `route.methods` except HEAD/OPTIONS, `(METHOD, route.path) -> action` when any dependency in the route's dependant tree (recursive) has a `call` with attribute `action`. Routes with no such dependency are absent.
  - `assert_gated(app, who, routes)`: `who` is the mutable dict that the test's `require_auth` override returns (fields `sub`, `role`, `project_id`, `domain_admin`, ...); `routes` is an iterable of `(METHOD, path_template, action)`. For each route it fills every `{name}` in the path with `"x"`, sends `json={}`, with `TestClient(app, raise_server_exceptions=False)`. With `role` `member`, with `role` `member` plus `domain_admin: True`, and with `role` `reader`, the status must be 403; for member and domain admin the body must equal `{"detail": "Policy does not allow <action>"}`. With `role` `admin` the status must not be 403.
- Consumes: nothing from other tasks.

- [ ] **Step 1: Write the failing tests** in `tests/shared/test_policy_engine_pr2.py`: `test_the_table_has_57_actions_35_of_them_new_and_role_admin` (the 35 names are in the file as a literal set; assert each maps to `"role:admin"`, none starts with `identity:`, all match `^[a-z-]+:[a-z-]+:[a-z-]+$`); `test_the_table_is_read_only` (`TypeError` on item assignment); `test_check_allows_an_admin_and_refuses_a_member_with_the_policy_text`; `test_check_raises_keyerror_for_an_unknown_action`; `test_check_follows_the_policy_file` (monkeypatch `CLOUDX_POLICY_FILE` to a tmp file with `"compute:flavor:create": "role:member"` and `policy.reset()`; member allowed); `test_require_marks_its_dependency_with_the_action`; `test_route_actions_reads_the_action_of_a_route` (a throwaway FastAPI app with one gated route); `test_a_leftover_cache_or_a_real_policy_file_does_not_leak_into_the_next_test` (two tests in order: the first writes a file that closes `compute:flavor:create` for admins and loads it via the env var; the second asserts `policy.sources()` has no `"file"` value and the env var points to a path that does not exist).
- [ ] **Step 2: Run them; expect failures** (`pytest tests/shared/test_policy_engine_pr2.py -q`).
- [ ] **Step 3: Implement** the table, `check()` and the `.action` attribute in `services/shared/policy.py`. Define the 35 names in service groups with `_ADMIN`; wrap the merged dict in `MappingProxyType`. Update every place that treated the table as a mutable `dict` (type hints, `dict(...)` copies; `GET /policy` must still serialise: check `services/auth/app/routers/policy.py`).
- [ ] **Step 4: Add the autouse fixture** `_isolate_policy` to `tests/conftest.py`: per test, `monkeypatch.setenv("CLOUDX_POLICY_FILE", str(tmp_path / "absent-policy.yaml"))`, then `reset()` on every loaded copy of the policy module (`shared.policy`, `services.shared.policy`, found in `sys.modules`; import nothing that is not already imported), before and after the test.
- [ ] **Step 5: Write `tests/gates.py`** as specified in Interfaces.
- [ ] **Step 6: Update the six "exactly 22" assertions** to the new facts: `test_defaults_are_the_22_actions_of_the_spec` becomes the 22 identity actions are unchanged inside the 57; the parity test's union check covers the identity set only; `test_policy_cli.py` counts (`defaults` output lines, sample equals `policy.defaults_yaml()`) use 57; `test_policy_endpoints.py` compares `GET /policy` with `dict(DEFAULT_RULES)`; `test_every_default_action_is_used_by_a_route` is limited to actions starting with `identity:`. Regenerate `etc/cloudx/policy.yaml.sample` from `policy.defaults_yaml()` (the engine is the source: `python -c "from shared import policy; open('etc/cloudx/policy.yaml.sample','w').write(policy.defaults_yaml())"` from `services/` on `PYTHONPATH`).
- [ ] **Step 7: Run** the new file, `tests/shared`, `tests/auth` (`pytest tests/shared tests/auth -q`) and the CI ruff command; expect all green.
- [ ] **Step 8: Commit** `feat(policy): 35 admin actions of the other services in the table, check() and the action marker`.

---

### Task 2: compute (12 routes)

**Files:**
- Modify: `services/compute/app/routers/flavors.py`, `aggregates.py`, `ops.py`
- Test: `tests/compute/test_policy_gates.py`

**Interfaces:**
- Consumes: `policy.require`, `tests/gates.py` (`route_actions`, `assert_gated`).
- Produces: nothing for later tasks.

Sites (by function): `create_flavor`, `set_extra_specs`, `delete_extra_spec`, `delete_flavor` (`flavors.py`); `create_aggregate`, `update_aggregate`, `delete_aggregate`, `aggregate_action` (`aggregates.py`); `update_quota_set`, `update_service` (`AdminOrInternal`), `migrate` (parameter `current`, used by the handler: keep it), `evacuate` (`AdminOrInternal`, parameter `current`, used) in `ops.py`. Actions: `compute:flavor:create`, `compute:flavor:delete`, `compute:flavor-extra-spec:set`, `compute:flavor-extra-spec:delete`, `compute:aggregate:create|update|delete|action`, `compute:quota:set`, `compute:service:update`, `compute:server:migrate`, `compute:server:evacuate`.

- [ ] **Step 1: Write the failing tests** in `tests/compute/test_policy_gates.py`: `test_each_gated_route_has_its_action` (the 12 `(METHOD, path, action)` entries as a literal dict equal to `route_actions(app)` restricted to the `compute:` prefix, so the set is exactly 12); `test_member_domain_admin_and_reader_are_refused_and_admin_is_not` (`assert_gated`, with `require_auth` overridden to return a mutable `who`, like `tests/compute/test_ops.py`); `test_internal_token_passes_update_service_and_evacuate_without_any_override` (set `INTERNAL_TOKEN`, no override, header `X-Internal-Token`, status not 401/403); `test_a_wrong_internal_token_is_refused`; `test_a_policy_file_can_open_flavor_create_to_members` and `test_a_policy_file_can_close_flavor_create_for_admins` (file via `CLOUDX_POLICY_FILE` + `policy.reset()`, `monkeypatch` of `policy._monotonic` as in `tests/auth/test_policy_routes.py` to skip the 2 s wait); `test_the_existing_ops_tests_still_pass` is not a test: run `tests/compute` in Step 4.
- [ ] **Step 2: Run; expect failures.**
- [ ] **Step 3: Convert the 12 sites** per the Global Constraints (keep names/positions; `update_service` and `evacuate` pass `authenticate=require_user_or_internal`).
- [ ] **Step 4: Run** `pytest tests/compute -q` and the CI ruff command; all green.
- [ ] **Step 5: Commit** `feat(compute): the admin-only actions follow the policy file`.

---

### Task 3: block-store (6 routes + 2 inline)

**Files:**
- Modify: `services/block_store/app/routers/volume_types.py`, `quotas.py`, `volumes.py`, `volume_actions.py`
- Test: `tests/block_store/test_policy_gates.py`

**Interfaces:**
- Consumes: `policy.require`, `policy.check`, `tests/gates.py`.

Sites: `create_volume_type`, `update_volume_type`, `delete_volume_type`; `update_quota` (PUT) and `delete_quota`; `force_detach_volume`; in `volume_action`, the sub-actions `os-force_delete` and `os-reset_status`. Actions: `block-store:volume-type:create|update|delete`, `block-store:quota:set`, `block-store:quota:clear`, `block-store:volume:force-detach`, `block-store:volume:force-delete`, `block-store:volume:reset-status`. The inline tests (`current.get("role") != "admin"` -> 403) are replaced by `policy.check(<action>, current)` at the same place, after `require_volume(db, ref, current)`.

- [ ] **Step 1: Write the failing tests**: `test_each_gated_route_has_its_action` (the 6 route entries, `block-store:` prefix; the two inline names are absent from `route_actions`); `test_member_domain_admin_and_reader_are_refused_and_admin_is_not` via `assert_gated` for the 6; `test_force_detach_still_accepts_a_positional_admin_call` (`block_main.force_detach_volume("vol1", {"role": "admin"}, db)` as in `tests/block_store/test_volumes.py`, or the existing test is kept and cited); `test_force_delete_and_reset_status_refuse_a_member_on_their_own_volume_with_the_policy_text` (parametrized over both, 403 + `Policy does not allow ...`); `test_a_member_on_another_projects_volume_gets_404_not_403` (both sub-actions); `test_admin_reaches_both_sub_actions`; `test_a_member_can_still_use_extend_and_migrate_sub_actions` (the dispatcher route is not gated as a whole); `test_a_policy_file_can_open_force_delete_to_members`; `test_list_volume_types_still_runs_for_a_member` (the `_: CurrentUser = None` signature).
- [ ] **Step 2: Run; expect failures.**
- [ ] **Step 3: Convert** the 6 route sites and the 2 inline sites.
- [ ] **Step 4: Run** `pytest tests/block_store -q` and ruff; all green.
- [ ] **Step 5: Commit** `feat(block-store): the admin-only actions follow the policy file`.

---

### Task 4: placement (5 routes)

**Files:**
- Modify: `services/placement/app/main.py`
- Test: `tests/placement/test_policy_gates.py`

**Interfaces:** Consumes `policy.require`, `tests/gates.py`.

Sites: `create_rp`, `delete_rp`, `set_inventories`, `create_allocations`, `delete_allocations`. Actions: `placement:resource-provider:create|delete`, `placement:inventory:set`, `placement:allocation:create|delete`. `set_inventories_internal` calls `set_inventories(name, payload, None, db)`; `create_rp` calls `create_rp_internal(payload, db)`: neither changes.

- [ ] **Step 1: Write the failing tests**: route mapping (5), `assert_gated`, `test_set_inventories_still_takes_none_in_the_admin_slot` (the call in `tests/placement/test_resource_providers.py` stays; add one that goes through `PUT /internal/resource_providers/{name}/inventories` with `require_internal` overridden and expects success), `test_the_internal_door_is_not_governed_by_the_file` (a file that closes `placement:inventory:set` for everyone with `"!"` leaves the `/internal/...` route working), `test_a_policy_file_can_close_resource_provider_delete_for_admins`.
- [ ] **Step 2: Run; expect failures.**
- [ ] **Step 3: Convert** the 5 sites.
- [ ] **Step 4: Run** `pytest tests/placement -q` and ruff; all green.
- [ ] **Step 5: Commit** `feat(placement): the admin-only actions follow the policy file`.

---

### Task 5: billing and telemetry (5 routes)

**Files:**
- Modify: `services/billing/app/main.py`, `services/telemetry/app/main.py`
- Test: `tests/billing/test_policy_gates.py`, `tests/telemetry/test_policy_gates.py`

**Interfaces:** Consumes `policy.require`, `tests/gates.py`.

Sites: billing `create_rating_sheet`, `update_rating_sheet`, `create_usage_record`; telemetry `create_metric`, `evaluate_now`. Actions: `billing:rating-sheet:create|update`, `billing:usage-record:create`, `telemetry:metric:create`, `telemetry:alarm:evaluate`. The four bulk routes (`AdminOrInternal` since #113) stay as they are.

- [ ] **Step 1: Write the failing tests** per service: route mapping, `assert_gated` (these services have no `require_auth` override in their old tests; use an override in the new ones, or signed tokens as `tests/billing` does, whichever the existing tests of the service use), `test_the_bulk_routes_are_not_governed_by_the_file` (a `"!"` for `billing:usage-record:create` leaves `POST /billing/usage-records/batch` working for the internal token), and billing's domain-scoped token (`ACCEPT_DOMAIN_SCOPE`): a domain-admin-scoped token is refused with the policy text.
- [ ] **Step 2: Run; expect failures.**
- [ ] **Step 3: Convert** the 5 sites.
- [ ] **Step 4: Run** `pytest tests/billing tests/telemetry -q` and ruff; all green.
- [ ] **Step 5: Commit** `feat(billing,telemetry): the admin-only actions follow the policy file`.

---

### Task 6: networking, object-store, ha (5 routes)

**Files:**
- Modify: `services/networking/app/routers/qos.py`, `services/networking/app/routers/subnet_pools.py`, `services/object_store/app/main.py`, `services/ha/app/main.py`
- Test: `tests/networking/test_policy_gates.py`, `tests/object_store/test_policy_gates.py`, `tests/ha/test_policy_gates.py`

**Interfaces:** Consumes `policy.require`, `tests/gates.py`.

Sites: `delete_qos_policy`, `delete_subnet_pool`, `create_bucket_type`, `delete_bucket_type`, `reset_host`. Actions: `networking:qos-policy:delete`, `networking:subnet-pool:delete`, `object-store:bucket-type:create|delete`, `ha:host:reset`.

- [ ] **Step 1: Write the failing tests**: per service route mapping and `assert_gated`. Object-store has no HTTP auth test today and uses `OptionalAuth` on other routes: the new test covers only the two gated routes. `test_a_member_can_still_create_a_qos_policy_but_not_delete_one` (the asymmetry is kept). The ha test follows `tests/ha/test_ha_api.py` (module reload, `who` dict).
- [ ] **Step 2: Run; expect failures.**
- [ ] **Step 3: Convert** the 5 sites.
- [ ] **Step 4: Run** `pytest tests/networking tests/object_store tests/ha -q` and ruff; all green.
- [ ] **Step 5: Commit** `feat(networking,object-store,ha): the admin-only actions follow the policy file`.

---

### Task 7: Whole-table tests, docs, changelog

**Files:**
- Create: `tests/shared/test_policy_coverage.py`
- Modify: `docs/policy.md`, `docs/feature-comparison.md`, `CHANGELOG.md`, `etc/cloudx/policy.yaml.sample` (regenerated again if the table changed since Task 1)

**Interfaces:** Consumes every service's app.

- [ ] **Step 1: Write the failing test** `test_every_action_is_used_by_a_route_or_is_a_known_inline_action`: import each of auth, compute, block_store, placement, billing, networking, object_store, telemetry, ha through `fresh_service` (as the service tests do), then assert `policy.required_actions() | {"block-store:volume:force-delete", "block-store:volume:reset-status"} == set(policy.DEFAULT_RULES)`. Add `test_no_service_keeps_an_admin_user_gate_on_a_converted_route` only if a simple check exists: skip it otherwise.
- [ ] **Step 2: Run; expect it to fail only if a task missed a site.**
- [ ] **Step 3: Update `docs/policy.md`**: opening paragraph and every "22" (57 in all, "22 identity actions" where it means auth-api); a section per service with the table action / route / built-in rule (routes as in the spec's inventory); the limits section: what stays in code (ownership, visibility, state checks with an admin bypass, internal routes, the bulk routes), the lock-out cases (internal token on `compute:service:update`/`compute:server:evacuate`, `compute:quota:set` forwards to `identity:project:quota-set` so both must allow, placement's `/internal/...` second door is not governed), and that the file must now exist, identical, on every node that runs any API service. `GET /policy` and `POST /policy/enforce` are auth-api's and answer for all 57 actions.
- [ ] **Step 4: Update `CHANGELOG.md`** (Unreleased): Added: the 35 actions; Changed: the 403 text of the converted routes is `Policy does not allow <action>`. Update `docs/feature-comparison.md`. Regenerate the sample if needed.
- [ ] **Step 5: Run** `pytest tests/shared -q` and ruff; green.
- [ ] **Step 6: Commit** `docs(policy): the file now covers every service's admin-only actions`.

---

### Task 8: Verification

**Files:** none (fixes go in the file they belong to).

- [ ] **Step 1:** Run the complete suite (`pytest -q`, about 10 to 12 minutes) and the exact CI lint command. Expect zero failures.
- [ ] **Step 2:** Run `cloudx-manage policy check` against a temporary file that sets a rule for one new action and one typo; confirm the output names the typo and lists the differing action; run `cloudx-manage policy defaults | diff - etc/cloudx/policy.yaml.sample` and expect no difference.
- [ ] **Step 3:** `grep -rn "AdminUser\|AdminOrInternal" services --include=*.py` outside auth/shared: every remaining use must be a site the spec leaves in code (the four bulk routes, none of the 35). List the remaining ones in the report.
- [ ] **Step 4:** Report; no commit unless a fix was needed.
