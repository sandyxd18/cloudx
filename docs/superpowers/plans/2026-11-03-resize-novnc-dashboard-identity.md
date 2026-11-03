# Resize auto-confirm, noVNC proxy, network pages, Identity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship resize auto-confirm, a packaged token-authenticated noVNC proxy, dashboard pages for trunks/QoS/subnet pools, and Identity domain/system tokens, credential rotation and trusts in one PR.

**Architecture:** Each part is its own commit. Resize: conductor timestamps `VERIFY_RESIZE`, compute exposes an internal list, the reaper confirms through the public endpoint. noVNC: websockify on each compute node, self-contained signed tokens, no cross-node state. Identity: extend `_create_token` and the auth routers; one shared-middleware guard for domain tokens.

**Tech Stack:** Python 3 / FastAPI / SQLAlchemy / Alembic / pytest, python-jose, websockify, React + Cloudscape (Vite), systemd + `scripts/cloudx_deb` packaging.

**Spec:** `docs/superpowers/specs/2026-11-03-resize-novnc-dashboard-identity-design.md`

## Global Constraints

- Docs and code comments in English.
- Branch `feat/resize-autoconfirm-novnc-identity`; commit trailers: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_015ZZ3VowHusnEAJzUhJtVob`.
- No version bump and no release notes in this PR.
- Policy files (oslo.policy style) are out of scope.
- `RESIZE_CONFIRM_WINDOW_SECONDS` default `86400`, `0` disables. Console token TTL `300` s. noVNC port `6080`.
- Existing behavior must not change when new inputs are absent: `POST /auth/tokens` without `scope` stays project-scoped; reaper keeps working when compute is not in the catalog.
- Run `pytest -q` for the touched test directories after every task; run the whole suite in Task 11.

## Review Focus

1. A console token minted for node A replayed against node B's proxy must be refused (host claim) — Task 4.
2. A domain-scoped token sent to a project-only service must give 403, not a 500 from a missing `project_id` — Task 7.
3. The reaper's `confirmResize` returning 409 (server confirmed or reverted between list and call) must be logged and skipped, not retried in a loop or crash the pass — Task 2.
4. Rotating twice inside a grace period: the first secret must be dead, only the previous one still works — Task 8.
5. A trust with `remaining_uses=1` used twice (including two concurrent requests) must issue exactly one token — Task 9.

---

### Task 1: `resize_pending_since` on servers

**Files:**
- Modify: `services/compute/app/models.py` (Server, next to `original_status`)
- Create: `services/compute/migrations/versions/compute_0005_resize_pending_since.py`
- Modify: `services/conductor/app/main.py` (`handle_update_server`)
- Test: `tests/conductor/test_resize_pending.py` (follow the harness in `tests/conductor/test_replication_conductor.py`)

**Interfaces:**
- Produces: `Server.resize_pending_since: Mapped[datetime | None]`; conductor invariant: it is non-NULL exactly while `status == "VERIFY_RESIZE"`.

- [ ] **Step 1: Write failing tests** in `tests/conductor/test_resize_pending.py`: `test_entering_verify_resize_sets_timestamp`, `test_leaving_verify_resize_clears_timestamp` (status → `ACTIVE`), `test_other_updates_leave_timestamp_alone` (update `error_message` only while pending; value unchanged), `test_migration_backfills_existing_verify_resize_rows` (run `compute_0005` upgrade on a DB with one `VERIFY_RESIZE` and one `ACTIVE` row; first gets a value, second NULL).
- [ ] **Step 2: Run** `pytest tests/conductor/test_resize_pending.py -v`. Expected: FAIL (column missing).
- [ ] **Step 3: Add the column** to `Server` and write migration `compute_0005` (`down_revision = "compute_0004"`; add column idempotently like `auth_0004` does with `_cols`; backfill `UPDATE compute_servers SET resize_pending_since = <now> WHERE status = 'VERIFY_RESIZE'`).
- [ ] **Step 4: In `handle_update_server`**, when `"status"` is in `safe_fields`, append `resize_pending_since = :_now` if the status is `VERIFY_RESIZE`, else `resize_pending_since = NULL`. Do not add the column to `ALLOWED_SERVER_FIELDS`.
- [ ] **Step 5: Run** the new tests plus `pytest tests/conductor tests/compute -q`. Expected: PASS.
- [ ] **Step 6: Commit** `feat(compute): record when a server entered VERIFY_RESIZE`.

### Task 2: Reaper confirms stale resizes

**Files:**
- Modify: `services/compute/app/main.py` or the router module that holds other `/internal/*` routes (find with `grep -rn "InternalAuth" services/compute`; if none, import `InternalAuth` from `shared.auth_middleware` as `services/database/app/main.py` does)
- Modify: `services/reaper/app/main.py`
- Test: `tests/compute/test_resize_pending_internal.py`, `tests/reaper/test_reaper.py`

**Interfaces:**
- Consumes: `Server.resize_pending_since` (Task 1).
- Produces: `GET /internal/resize-pending?seconds=<int>` → `{"items": [{"id": str}]}` for servers with `status == "VERIFY_RESIZE"` and `resize_pending_since <= now - seconds`; NULL timestamps never returned. Reaper: `RESIZE_CONFIRM_WINDOW: int` (env `RESIZE_CONFIRM_WINDOW_SECONDS`, default 86400) and `resize_confirms(token: str, base: str) -> None`, called from `cycle()` after the existing loop when the window is > 0 and `catalog.url("compute")` resolves.

- [ ] **Step 1: Write failing tests.** Compute: `test_returns_only_servers_past_the_window`, `test_excludes_null_timestamp`, `test_requires_internal_auth`. Reaper (extend `Fake.request` and the `_peers` fixture with `COMPUTE_API_URL`; existing tests must keep passing): `test_confirms_servers_past_the_window` (asserts `POST .../servers/<id>/confirmResize` with `X-Auth-Token: T`), `test_window_zero_disables_the_sweep`, `test_dry_run_only_logs`, `test_confirm_409_is_skipped_and_the_rest_continue` (first id answers 409, second id still confirmed, `cycle()` does not raise), `test_compute_missing_from_catalog_does_not_break_the_cycle`.
- [ ] **Step 2: Run** both files. Expected: FAIL.
- [ ] **Step 3: Implement the endpoint** (compute) and `resize_confirms` + `cycle()` call (reaper). Read `require_server` to confirm an admin token may confirm any project's server; if it cannot, report it instead of widening permissions.
- [ ] **Step 4: Run** `pytest tests/compute tests/reaper -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(reaper): confirm resizes left in VERIFY_RESIZE past a window`.

### Task 3: Console token and proxy plugin

**Files:**
- Create: `services/shared/console_token.py`
- Create: `services/compute_worker/app/console_proxy.py`
- Test: `tests/shared/test_console_token.py`, `tests/compute_worker/test_console_proxy.py`

**Interfaces:**
- Produces: `console_token.issue_token(server_id: str, host: str, vnc_port: int, ttl: int = 300) -> str`; `console_token.verify_token(token: str, host: str) -> int` returning the VNC port, raising `ValueError` on bad signature, expiry, `typ != "console"`, or `host` mismatch. `console_proxy.ConsoleTokenPlugin(src)` with `lookup(token: str) -> tuple[str, int] | None` returning `("127.0.0.1", port)` or `None` (websockify's contract; duck-typed, no websockify import). The plugin's own host comes from env `COMPUTE_HOST`; the secret from env `AUTH_SECRET_KEY`.

- [ ] **Step 1: Write failing tests:** `test_roundtrip_returns_the_port`, `test_expired_token_rejected` (ttl=-1), `test_wrong_host_rejected`, `test_wrong_type_rejected` (a normal auth JWT signed with the same key), `test_tampered_signature_rejected`; plugin: `test_lookup_returns_loopback_target`, `test_lookup_returns_none_for_invalid`.
- [ ] **Step 2: Run.** Expected: FAIL (modules missing).
- [ ] **Step 3: Implement** with `jose.jwt` HS256 and claims `typ=console`, `sid`, `host`, `port`, `exp`. Plugin calls `load_service_config("compute-worker")` once at construction.
- [ ] **Step 4: Run** the two test files. Expected: PASS.
- [ ] **Step 5: Commit** `feat(console): signed console tokens and websockify token plugin`.

### Task 4: console-url returns the proxy URL

**Files:**
- Modify: `services/compute_worker/app/main.py` (`handle_get_console_info`) and `services/compute_worker/app/libvirt_driver.py` only if needed
- Create: `services/compute/app/console.py`
- Modify: `services/compute/app/routers/server_ops.py` (`console_url`, `remote_console`), `services/compute/app/config.py`
- Modify: `etc/cloudx/cloudx.conf.sample` (document `console_proxy_host` beside the compute-worker notes at the end of the file)
- Test: `tests/compute/test_console_url.py`; update `tests/compute/test_console_websocket.py` and `tests/compute/test_ops.py` if they assert the old URL

**Interfaces:**
- Consumes: `issue_token` (Task 3).
- Produces: worker `get_console_info` result gains `proxy_host: str` (config `console_proxy_host`, default `COMPUTE_HOST`) and `host: str` (`COMPUTE_HOST`). `console.novnc_url(server_id: str, vnc: dict) -> str | None` returns `http://<proxy_host>:6080/vnc.html?autoconnect=1&path=websockify%3Ftoken=<jwt>` or `None` when `vnc` has no `port`/`proxy_host`. `NOVNC_BASE_URL` is removed from config.

- [ ] **Step 1: Write failing tests:** `test_console_url_contains_a_token_for_the_servers_host_and_port` (decode the token with `verify_token(token, host)` and assert the port), `test_console_url_without_vnc_port_has_no_novnc_url` (`novnc_url` is `None`, response still 200), `test_remote_consoles_uses_the_same_url`, `test_token_issued_for_node_a_is_refused_for_node_b` (Review Focus 1: `verify_token(token, "node-b")` raises).
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement** `console.py`; use it in both endpoints; add `proxy_host`/`host` to the worker handler; update the stale tests.
- [ ] **Step 4: Run** `pytest tests/compute tests/compute_worker -q`. Expected: PASS.
- [ ] **Step 5: Read `services/dashboard/frontend/src/pages/Console.jsx`**; make it use the URL from `console-url` (key `novnc_url`) if it builds its own; delete the text claiming a proxy "on port 6080" only if it is now wrong.
- [ ] **Step 6: Commit** `feat(console): console-url returns a token URL for the node's proxy`.

### Task 5: Package the proxy unit

**Files:**
- Create: `etc/systemd/cloudx-novnc-proxy.service`
- Modify: `packaging/manifest.toml`, `packaging/constraints.txt`, `requirements.txt` (add `websockify`, pinned to a version at least two weeks old)
- Modify: `scripts/install-systemd.sh` only if it enumerates unit files
- Test: `tests/packaging/*` (update `golden/` as those tests' own helper directs)

**Interfaces:**
- Produces: unit `novnc-proxy`: `kind = "unit"`, `bundle_with = "compute-worker"`, `roles = []`, `depends = ["novnc"]`. `ExecStart=/opt/cloudx/venv/bin/python -m websockify --web /usr/share/novnc --token-plugin console_proxy.ConsoleTokenPlugin 6080`, `Environment=PYTHONPATH=/opt/cloudx/services/compute_worker/app:/opt/cloudx/services`, `User=root`, `Restart=always`.

- [ ] **Step 1: Add the manifest entry and unit file**, modeled on `named-resolver` (unit) and `compute-worker.service` (paths).
- [ ] **Step 2: Run** `pytest tests/packaging -q`. Fix `test_manifest`, `test_constraints`, `test_render`, golden files until PASS; the failures show what each expects.
- [ ] **Step 3: Build** `scripts/build-deb.sh` (as `tests/packaging/test_build.py` does) and confirm `cloudx-compute-worker` now contains `cloudx-novnc-proxy.service` and `Depends` includes `novnc`.
- [ ] **Step 4: Commit** `feat(packaging): novnc-proxy unit on compute nodes`.

### Task 6: Dashboard pages — Trunks, QoS, Subnet pools

**Files:**
- Modify: `services/dashboard/frontend/src/api.js`, `src/App.jsx`, `src/components/Layout.jsx` (nav near the Routers link, breadcrumb map, search list)
- Create: `src/pages/Trunks.jsx`, `src/pages/QosPolicies.jsx`, `src/pages/SubnetPools.jsx`
- Modify: `docs/feature-comparison.md` (remove the "API and CLI only" row)

**Interfaces:**
- Produces: routes `/trunks`, `/qos`, `/subnet-pools`; api functions `getTrunks()`, `getTrunk(ref)`, `createTrunk(body)`, `deleteTrunk(ref)`, `addTrunkSubports(ref, body)`, `removeTrunkSubports(ref, body)` on `/networking/trunks` (`PUT .../add_subports|remove_subports`). QoS and subnet pool calls already exist (`getQosPolicies`, `createQosPolicy`, `deleteQosPolicy`, `createQosBwRule`, `getSubnetPools`, `createSubnetPool`, `deleteSubnetPool`).

Each page is a Cloudscape table with `TextFilter`, create through a `Modal` (as `InterfaceModal` in `Routers.jsx`), delete through `DeleteModal`. Field names for forms come from `services/networking/app/schemas.py` (`TrunkIn`, `TrunkSubPortsPayload`, QoS and subnet-pool schemas). One commit per page.

- [ ] **Step 1: Trunks page** — columns name, parent port, subport count, status; actions create (name, parent port select, optional subports), delete, add/remove subport. Add the api functions and route/nav entries. Verify `npm run build` in `services/dashboard/frontend` succeeds. Commit `feat(dashboard): trunks page`.
- [ ] **Step 2: QoS page** — policy list with create/delete and an "Add bandwidth rule" modal (`createQosBwRule`). Build. Commit `feat(dashboard): QoS policies page`.
- [ ] **Step 3: Subnet pools page** — list with create (prefixes, min/default/max prefix length) and delete. Build. Commit `feat(dashboard): subnet pools page`.
- [ ] **Step 4: Run** `pytest tests/docs tests/dashboard -q` after the docs row is removed. Expected: PASS.

### Task 7: Domain and system scoped tokens

**Files:**
- Modify: `services/auth/app/schemas.py` (`LoginIn.scope`), `services/auth/app/tokens.py` (`_create_token`), `services/auth/app/routers/tokens.py` (`login`)
- Modify: `services/shared/auth_middleware.py` (`require_auth`), `services/billing/app/main.py`
- Test: `tests/auth/test_scoped_tokens.py`, `tests/shared/test_domain_scope_guard.py`

**Interfaces:**
- Produces: `class ScopeIn(BaseModel)` with `project: str | None`, `domain: str | None`, `system: bool = False`; `LoginIn.scope: ScopeIn | None`. `_create_token(user, scope_type="project", extra_roles=None, project_name=None, role=None, *, project_id: str | None = None, domain_id: str | None = None) -> str` where `project_id=None` means `user.project_id`, `""` means none; `scope_type="domain"` forces `domain_admin: true`.
- Rules: more than one field set in `scope` → 400. `scope.project` behaves like the existing `project` field. Domain scope: allowed when `user.is_domain_admin` and `domain` equals the user's domain (by id or name), or `role == admin`; else 403; token has `scope_type: "domain"`, `project_id: ""`, `domain_id`. System scope: `role == admin` only, else 403; token has `scope_type: "system"`, `role: "admin"`, the user's home `project_id`. `require_auth` raises 403 `project-scoped token required` for `scope_type == "domain"` unless `os.getenv("ACCEPT_DOMAIN_SCOPE")` is true at call time; billing sets `os.environ.setdefault("ACCEPT_DOMAIN_SCOPE", "true")` after its config load.

- [ ] **Step 1: Write failing tests** (`tests/auth/test_scoped_tokens.py`): `test_no_scope_is_project_scoped_as_before`, `test_domain_scope_for_domain_admin`, `test_domain_scope_for_other_domain_is_403`, `test_domain_scope_for_plain_member_is_403`, `test_system_scope_for_admin`, `test_system_scope_for_non_admin_is_403`, `test_two_scope_fields_is_400`, `test_domain_token_can_list_users_of_its_domain_in_auth_api`. Shared: `test_domain_token_is_403_in_a_project_only_service` (route reading `current["project_id"]`; assert 403 and not 500), `test_domain_token_passes_when_service_opts_in`, `test_project_and_system_tokens_unaffected`.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement** schemas, `_create_token` parameters, the `login` branches, the guard, and billing's opt-in. If a billing endpoint reads `project_id` unguarded, it must tolerate `""`.
- [ ] **Step 4: Run** `pytest tests/auth tests/shared tests/billing -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(auth): domain and system scoped tokens`.

### Task 8: Application credential rotation

**Files:**
- Modify: `services/auth/app/models.py` (`ApplicationCredential`), `services/auth/app/schemas.py`, `services/auth/app/routers/app_credentials.py`
- Create: `services/auth/migrations/versions/auth_0008_appcred_rotation.py`
- Modify: `cli/cloudx_cli/commands/auth.py`
- Test: `tests/auth/test_app_credential_rotation.py`, `tests/cli/test_auth_appcred_rotate.py`

**Interfaces:**
- Produces: columns `previous_secret_hash: str | None`, `previous_valid_until: datetime | None`; `class RotateIn(BaseModel)` with `grace_seconds: int = Field(0, ge=0, le=86400)`; `POST /v3/users/{user_ref}/application_credentials/{cred_ref}/rotate` → 200 with `_appcred_to_dict(ac, secret=<new>)`; CLI `cloudx auth appcred rotate USER CRED [--grace-seconds N]`. Permission: self or admin, like the sibling routes.

- [ ] **Step 1: Write failing tests:** `test_rotate_returns_a_working_new_secret`, `test_old_secret_fails_immediately_with_grace_zero`, `test_old_secret_works_until_grace_ends` (monkeypatch `utcnow`), `test_rotating_twice_kills_the_first_secret` (Review Focus 4), `test_rotate_by_another_member_is_403`, `test_negative_grace_is_422`; CLI: `test_rotate_prints_the_new_secret_once` (mirror an existing CLI test's mocking).
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement** model columns, migration (`down_revision = "auth_0007"`, idempotent column adds), the route, the login check in `login_with_app_credential` (new secret first, then previous while `previous_valid_until > utcnow()`), and the CLI command.
- [ ] **Step 4: Run** `pytest tests/auth tests/cli -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(auth): rotate application credentials with a grace period`.

### Task 9: Trusts

**Files:**
- Modify: `services/auth/app/models.py`, `services/auth/app/schemas.py`, `services/auth/app/tokens.py`, `services/auth/app/main.py` (include the router)
- Create: `services/auth/migrations/versions/auth_0009_trusts.py`, `services/auth/app/routers/trusts.py`
- Test: `tests/auth/test_trusts.py`

**Interfaces:**
- Consumes: `_create_token` from Task 7.
- Produces: model `Trust` (`auth_trusts`: `id`, `trustor_user_id`, `trustee_user_id`, `project_id`, `roles` csv, `expires_at`, `remaining_uses: int | None`, `revoked: bool`, `created_at`); `class TrustIn(BaseModel)`: `trustee_user_id: str`, `project_id: str | None`, `roles: list[str]`, `expires_at: str | None`, `remaining_uses: int | None = Field(None, ge=1)`; `class TrustTokenIn(BaseModel)`: `trust_id: str`. `_create_token` gains kw-only `trust_id: str | None = None, trustee_id: str | None = None` that add those claims. Endpoints: `POST|GET /v3/OS-TRUST/trusts`, `GET|DELETE /v3/OS-TRUST/trusts/{id}` (trustor or admin), `POST /auth/tokens/trust` (caller is the trustee). Role order `reader < member < admin`; a user "holds" a role when `project_role(...)` ranks at least as high; the issued roles are the trust's roles still held, in the trust's order (first is the primary role).

- [ ] **Step 1: Write failing tests:** `test_create_requires_trustor_to_hold_the_roles`, `test_create_list_show_delete`, `test_only_trustor_or_admin_can_delete`, `test_token_is_limited_to_the_trust_roles_and_sub_is_the_trustor` (claims `trust_id`, `trustee_id`), `test_expired_trust_is_401`, `test_revoked_trust_is_401`, `test_other_user_cannot_use_the_trust`, `test_disabled_trustor_is_401`, `test_role_removed_from_trustor_shrinks_the_token_roles`, `test_remaining_uses_one_allows_exactly_one_token` (second call 401; Review Focus 5, with a second case using two threads or two sessions against the same row), `test_trust_token_cannot_create_a_trust` (403), `test_token_issue_writes_an_audit_entry_with_both_ids`.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement** model, migration (`down_revision = "auth_0008"`), router (audit via the helper the other routers use, see `routers/audit.py`), `_create_token` claims. Decrement `remaining_uses` with a conditional `UPDATE ... WHERE remaining_uses > 0` and check the row count, so concurrent use cannot double-spend.
- [ ] **Step 4: Run** `pytest tests/auth -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(auth): trusts for delegated tokens`.

### Task 10: Docs and changelog

**Files:**
- Modify: `docs/feature-comparison.md` (Identity rows: move scoped tokens, rotation, trust from Missing to supported, recompute the Identity estimate only if the file derives it from row counts; noVNC proxy row; resize row if one mentions auto-confirm), `docs/console.md`, `docs/user-guide-cli.md` (`auth appcred rotate`), the Identity doc that lists endpoints (find with `grep -rln "application_credentials" docs`), `CHANGELOG.md` (Unreleased)

- [ ] **Step 1: Edit the docs** to match the behavior built in Tasks 1–9, including `console_proxy_host`, port 6080 on compute nodes, and `RESIZE_CONFIRM_WINDOW_SECONDS`.
- [ ] **Step 2: Run** `pytest tests/docs -q` and `python scripts/check_feature_comparison.py`, `python scripts/check_doc_commands.py`. Expected: PASS.
- [ ] **Step 3: Commit** `docs: resize auto-confirm, console proxy, scoped tokens, rotation, trusts`.

### Task 11: Whole-suite check

- [ ] **Step 1: Run** `pytest -q` from the repo root. Expected: PASS (report any failure with its output; do not edit unrelated tests to pass).
- [ ] **Step 2: Run** `npm run build` in `services/dashboard/frontend`. Expected: success.

### Task 12: Lab verification (needs the lab; run with the user)

- [ ] **Step 1: Resize:** set `RESIZE_CONFIRM_WINDOW_SECONDS=120` on the reaper, resize a VM, wait two intervals; the VM leaves `VERIFY_RESIZE` to `ACTIVE` without a manual confirm.
- [ ] **Step 2: Console:** on a VM on the controller and on a VM on another compute node, `console-url`, open the URL in a browser, see the console. Reuse a token after 5 minutes and against the other node; both must fail.
- [ ] **Step 3: Dashboard:** create and delete a trunk, a QoS policy with a rule, and a subnet pool from the pages.
- [ ] **Step 4: Identity:** domain-scoped login as a domain admin; system-scoped login as admin; rotate a credential with `--grace-seconds 30` and use both secrets; create a trust and use it from the trustee.
- [ ] **Step 5: Record** results in `docs/releases/`-style notes only if the user asks; report outcomes either way.
