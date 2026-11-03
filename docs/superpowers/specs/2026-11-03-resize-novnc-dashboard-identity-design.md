# Resize auto-confirm, noVNC proxy, network dashboard pages, Identity scopes/rotation/trust

Date: 2026-11-03 · Branch: `feat/resize-autoconfirm-novnc-identity` · Base: 1.0.9

## Goal

Close four open gaps from the 1.0.9 backlog in one PR, each usable on the lab and not only present as an endpoint:

1. A server left in `VERIFY_RESIZE` is confirmed automatically after a window.
2. The noVNC console works out of the box: a packaged proxy with per-session tokens.
3. Dashboard pages for trunks, QoS policies and subnet pools.
4. Identity: domain/system scoped tokens, application credential rotation, trusts.

Work is split into one commit per part so each can be read and reverted alone.

## Out of scope

- **Policy files (oslo.policy-style rules) in every service.** Decided to ship as its own PR. Finding that drives this: `shared.policy.enforce` is called only by the debug endpoint `POST /policy/enforce`; real checks are `role == "admin"` written in each router. A policy file only matters once routers call `enforce`, which touches every service.
- Federation/SAML/OAuth2, MFA, Fernet tokens.
- Live migration, QoS enforcement on LVM.
- A dashboard page for application credentials.

## Part 1: Resize auto-confirm

`confirmResize` and `revertResize` already exist (`services/compute/app/routers/server_actions.py`). Only the timeout is missing.

- `services/reaper` gets a new sweep. It lists servers in `VERIFY_RESIZE` whose status has not changed for longer than `RESIZE_CONFIRM_WINDOW_SECONDS` (default `86400`; `0` disables) and calls the existing `POST /servers/{id}/confirmResize` with its admin token. No new confirm path.
- Servers that an older version left `ACTIVE` with `original_flavor` set are accepted by `confirm_resize` today. The sweep includes them only if compute exposes them through the same list (see plan check).
- The window needs a reliable "in VERIFY_RESIZE since" time. If `updated_at` also moves for unrelated reasons, add `resize_pending_since` (compute migration) set when the worker enters `VERIFY_RESIZE`. Plan check 1 decides.
- Reaper runs every `REAPER_INTERVAL` (600 s), so confirmation happens up to one interval after the window. `REAPER_DRY_RUN` is honored (log only).
- Tests: unit tests for the sweep (inside window untouched, past window confirmed, `0` skipped, dry-run, confirm failure retried next pass). One lab check: resize, wait past a short window, observe `ACTIVE`.

## Part 2: noVNC proxy

Today `console-url` returns `NOVNC_BASE_URL` (default `http://localhost:6080/vnc.html`) and no package starts anything on 6080.

- Package: a systemd unit runs `websockify` with the noVNC web assets on port 6080, added to `packaging/manifest.toml` and installed with compute-api on the controller.
- Auth: compute-api creates a short-lived console token (TTL a few minutes, single server) when `console-url` is requested, writes it to a token file that websockify reads (`token=host:port`), and returns a URL carrying the token. Port 6080 without a valid token connects to nothing.
- Token file lives in a directory only compute-api (writer) and websockify (reader) can access; expired entries are removed by compute-api on each write.
- Remote hypervisors: the mapped target is the VNC address on the node that runs the VM, not localhost.
- Dashboard: `Console.jsx` uses the URL returned by `console-url`; no other UI change.
- Risk: the remote-node path and inter-node firewall can only be confirmed on the lab. Verification must include a VM on a non-controller node.
- Tests: unit tests for token issue/expiry/file format and the `console-url` response; lab check with a VM on the controller and on a compute node.

## Part 3: Dashboard pages

- Three pages in `services/dashboard/frontend/src/pages`: Trunks (create, delete, add/remove subports), QoS policies (create, delete, add bandwidth rule), Subnet pools (create, delete). They follow `Routers.jsx` / `RouterCreate.jsx`.
- `api.js` already has QoS and subnet pool calls; trunk calls are added (`/networking/trunks`, `add_subports`, `remove_subports`).
- No backend change. Remove the "API and CLI only" row in `docs/feature-comparison.md`.
- Verification: frontend build succeeds; manual check against the lab. No UI test framework is introduced.

## Part 4: Identity

All in `services/auth` and `services/shared`, one migration `auth_0008`.

### 4a. Domain and system scoped tokens

- `POST /auth/tokens` accepts optional `scope`: `{"project": ...}`, `{"domain": ...}` or `{"system": true}`. Absent means today's behavior (project scoped), so CLI, dashboard and reaper do not change.
- Domain scope: allowed for a user with `is_domain_admin` in that domain, or admin; otherwise 403. Token has `scope_type: "domain"`, `domain_id`, `domain_admin: true`, no `project_id`.
- System scope: allowed only for `role == admin`; otherwise 403. Token has `scope_type: "system"`, `role: admin`, and keeps the admin's home `project_id` so services that read `project_id` behave as they do with today's admin token.
- Enforcement point: `require_auth` in `services/shared/auth_middleware.py` rejects `scope_type == "domain"` with 403 `project-scoped token required`, unless the service opts in with `ACCEPT_DOMAIN_SCOPE=true`. auth-api uses its own dependency and is unaffected; billing opts in because it already reads `domain_admin`.
- Tests: login matrix (project/domain/system × admin, domain admin, member), rejection in a project-only service, billing accepting a domain token.

### 4b. Application credential rotation

- `POST /v3/users/{user}/application_credentials/{cred}/rotate` with optional `grace_seconds` (default `0`). Returns the new secret once, like create. Same permission rule as the other credential routes (self or admin).
- Migration adds `previous_secret_hash` and `previous_valid_until` to `auth_application_credentials`. Login checks the new secret, then the previous one while `previous_valid_until` is in the future.
- CLI gets a `rotate` command. No dashboard page.
- Tests: rotate returns a working secret, old secret fails at once with grace 0, old secret works until the grace ends, rotate by another non-admin user is 403.

### 4c. Trust

- Table `auth_trusts`: id, `trustor_user_id`, `trustee_user_id`, `project_id`, `roles` (csv), `expires_at`, `remaining_uses` (nullable), `revoked`, `created_at`.
- Endpoints: `POST|GET|DELETE /v3/OS-TRUST/trusts` (trustor or admin), and `POST /auth/tokens/trust` where the trustee sends its own token plus `trust_id`.
- Create: trustor must hold every listed role in the project at that time.
- Token issue re-checks: trust not revoked, not expired, uses left (decremented), trustor enabled and still holding the roles; the granted roles are the intersection. Any failure is 401.
- Impersonation: the token's `sub` is the trustor, with claims `trust_id` and `trustee_id`. The audit log records both ids.
- No redelegation: a token with a `trust_id` claim cannot create a trust.
- Tests: create/list/delete, token roles limited to the trust roles, expiry, remaining uses, trustor disabled or role removed, redelegation refused, audit entry has both ids.

## Docs, packaging, release

- Update `docs/feature-comparison.md` (scoped tokens, rotation, trust rows; noVNC proxy row; dashboard row) and keep `tests/docs/test_feature_comparison.py` passing. Update `docs/console.md` and add the new endpoints to the Identity docs and CLI guide.
- `CHANGELOG.md` entry. Version bump and release notes follow the existing release flow and are a separate chore PR, not part of this one.
- Policy file PR is tracked as the next item after this one.

## Plan-stage checks (facts to confirm before coding)

1. Whether compute `updated_at` is reliable for "time in VERIFY_RESIZE"; otherwise add `resize_pending_since`.
2. Whether `GET /servers` (admin, all projects) gives the reaper everything it needs, or an `/internal/` listing is needed as for the other reaper sweeps.
3. Which services import `require_auth` from `shared` and which have their own dependency, to place `ACCEPT_DOMAIN_SCOPE` correctly.
4. Where the websockify and noVNC assets come from for the `.deb` (distro packages vs. vendored), given `packaging/constraints.txt`.
