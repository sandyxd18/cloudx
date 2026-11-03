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
- `compute_servers` has no `updated_at`, so a new nullable column `resize_pending_since` is added (migration `compute_0005`). The conductor sets it when a server's status becomes `VERIFY_RESIZE` and clears it on any other status. The migration backfills existing `VERIFY_RESIZE` rows with the migration time, so they are confirmed one window after the upgrade.
- compute-api gets `GET /internal/resize-pending?seconds=N` (internal auth, like the other services' `/internal/stale-errors`) returning the ids past the window. Rows with a NULL timestamp are never returned. Servers that an older version left `ACTIVE` with `original_flavor` set have no timestamp and are not swept.
- Reaper runs every `REAPER_INTERVAL` (600 s), so confirmation happens up to one interval after the window. `REAPER_DRY_RUN` is honored (log only).
- Tests: unit tests for the sweep (inside window untouched, past window confirmed, `0` skipped, dry-run, confirm failure retried next pass). One lab check: resize, wait past a short window, observe `ACTIVE`.

## Part 2: noVNC proxy

Today `console-url` returns `NOVNC_BASE_URL` (default `http://localhost:6080/vnc.html`) and no package starts anything on 6080. VNC is bound to `127.0.0.1` on the node that runs the VM (`libvirt_driver.py`) and placement records no node addresses, so a proxy on the controller cannot reach VMs on other nodes. Decision: run the proxy on every compute node and keep VNC on loopback.

- Package: unit `novnc-proxy` (`kind = "unit"`, `bundle_with = "compute-worker"`) runs websockify with the noVNC web assets on port 6080 on every node with the compute role.
- Token: compute-api issues a signed JWT (HS256, `auth_secret_key`, TTL 300 s) with claims `typ=console`, `sid` (server id), `host` (compute host name), `port` (VNC port). A websockify token plugin on the node checks signature, expiry, `typ`, and that `host` equals the node's own `COMPUTE_HOST`, then targets `127.0.0.1:<port>`. The `host` check stops a token for one node from opening a different VM's VNC port on another node. Nothing is synced between nodes. Port 6080 without a valid token connects to nothing.
- Address: each compute node sets `console_proxy_host` in `compute-worker.conf` (the address the browser can reach; defaults to `COMPUTE_HOST`). The worker returns it from `get_console_info`; compute-api builds `http://<console_proxy_host>:6080/vnc.html?autoconnect=1&path=websockify%3Ftoken=<jwt>` for both `console-url` and `remote-consoles`.
- Dashboard: `Console.jsx` uses the URL returned by the API; no other UI change.
- Risk: the browser must reach port 6080 on the compute nodes, and the exact noVNC URL parameters are confirmed on the lab. Verification must include a VM on a non-controller node.
- Tests: unit tests for token issue/verify (expiry, wrong host, wrong type, tampering), the plugin lookup, and the URL returned by both endpoints; lab check with VMs on the controller and on another compute node.

## Part 3: Dashboard pages

- Three pages in `services/dashboard/frontend/src/pages`: Trunks (create, delete, add/remove subports), QoS policies (create, delete, add bandwidth rule), Subnet pools (create, delete). They follow `Routers.jsx` / `RouterCreate.jsx`.
- `api.js` already has QoS and subnet pool calls; trunk calls are added (`/networking/trunks`, `add_subports`, `remove_subports`).
- No backend change. Remove the "API and CLI only" row in `docs/feature-comparison.md`.
- Verification: frontend build succeeds; manual check against the lab. No UI test framework is introduced.

## Part 4: Identity

All in `services/auth` and `services/shared`; two migrations (`auth_0008` rotation columns, `auth_0009` trusts table).

### 4a. Domain and system scoped tokens

- `POST /auth/tokens` accepts optional `scope`: `{"project": ...}`, `{"domain": ...}` or `{"system": true}`. Absent means today's behavior (project scoped), so CLI, dashboard and reaper do not change.
- Domain scope: allowed for a user with `is_domain_admin` in that domain, or admin; otherwise 403. Token has `scope_type: "domain"`, `domain_id`, `domain_admin: true`, no `project_id`.
- System scope: allowed only for `role == admin`; otherwise 403. Token has `scope_type: "system"`, `role: admin`, and keeps the admin's home `project_id` so services that read `project_id` behave as they do with today's admin token.
- Enforcement point: `require_auth` in `services/shared/auth_middleware.py` rejects `scope_type == "domain"` with 403 `project-scoped token required`, unless the service opts in with `ACCEPT_DOMAIN_SCOPE=true`. auth-api uses its own dependency and is unaffected; billing opts in because it already reads `domain_admin`.
- Tests: login matrix (project/domain/system × admin, domain admin, member), rejection in a project-only service, billing accepting a domain token.

### 4b. Application credential rotation

- `POST /v3/users/{user}/application_credentials/{cred}/rotate` with optional `grace_seconds` (default `0`). Returns the new secret once, like create. Same permission rule as the other credential routes (self or admin).
- Migration adds `previous_secret_hash` and `previous_valid_until` to `auth_application_credentials`. Login checks the new secret, then the previous one while `previous_valid_until` is in the future.
- CLI gets `auth appcred rotate` (the CLI has no application credential commands today; only `rotate` is added). No dashboard page.
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

## Plan-stage checks (resolved)

1. `updated_at` does not exist on servers: `resize_pending_since` is added (Part 1).
2. The reaper needs a new compute endpoint `/internal/resize-pending`; no existing list gives it (Part 1).
3. Billing uses `shared.auth_middleware.require_auth`; auth-api has its own dependency. Billing sets `ACCEPT_DOMAIN_SCOPE` in code; the shared middleware reads it at call time.
4. websockify is installed from the pinned Python constraints into the CloudX venv (the plugin needs the venv's `jose`); the noVNC web assets come from the apt package `novnc` (`depends` of the unit).
