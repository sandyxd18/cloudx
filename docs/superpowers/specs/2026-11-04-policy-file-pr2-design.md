# Policy file, PR 2: the admin-only actions of the other services

Date: 2026-11-04 · Delivery: one PR · Base: main after #113 · Extends: `2026-11-04-policy-file-design.md` (PR 1, merged as #110 and #111)

## Goal

The file `/etc/cloudx/policy.yaml` already decides 22 actions of auth-api. PR 2 puts the 35 other pure admin gates of the platform under the same file, so an operator can change who may call them without a code change or a restart. Without a file, every decision stays exactly what it is today.

## Decisions taken (with the project owner)

- **Scope: pure gates only.** A site is converted when its whole decision is "this role may do this" (`AdminUser` or `AdminOrInternal` on a route, or the same inline test). A site that mixes a role with ownership or visibility, or that is a state check with an admin bypass (a locked server, a quota readable by its own project), stays in code. The inventory found 35 pure gates, 97 mixed sites and 53 internal routes (`InternalAuth`, never policy).
- **One PR.** The mechanism exists; this PR is the repetition of one pattern in 8 services, plus the tests and docs that show nothing changed by accident.
- **Authn-only routes are not part of this PR.** The four bulk routes of billing and telemetry were fixed in #113 (`AdminOrInternal`); they are service-to-service calls and stay out of the file, because an operator who closed them would stop the workers. The remaining ones (unauthenticated image read, networking `external` and router gateway, placement writes) are separate fixes.
- **The rules are what they say.** As in PR 1, no hidden exceptions: `"!"` closes an action for admins and for the internal token too.

## The 35 actions

All have the built-in rule `role:admin`. `AdminUser` and `AdminOrInternal` both end in `claims["role"] == "admin"` and ignore `domain_admin`, so `role:admin` is the exact equivalent. The internal token becomes the synthetic claims `role: admin` and satisfies it.

Names follow `service:resource:verb`, kebab-case. PR 1's compound verbs (`identity:domain:quota-set`) keep their names; the new ones use single verbs.

| Service | Actions |
|---|---|
| compute (12) | `compute:flavor:create`, `compute:flavor:delete`, `compute:flavor-extra-spec:set`, `compute:flavor-extra-spec:delete`, `compute:aggregate:create`, `compute:aggregate:update`, `compute:aggregate:delete`, `compute:aggregate:action`, `compute:quota:set`, `compute:service:update`, `compute:server:migrate`, `compute:server:evacuate` |
| block-store (8) | `block-store:volume-type:create`, `block-store:volume-type:update`, `block-store:volume-type:delete`, `block-store:quota:set`, `block-store:quota:clear`, `block-store:volume:force-detach`, `block-store:volume:force-delete`, `block-store:volume:reset-status` |
| placement (5) | `placement:resource-provider:create`, `placement:resource-provider:delete`, `placement:inventory:set`, `placement:allocation:create`, `placement:allocation:delete` |
| billing (3) | `billing:rating-sheet:create`, `billing:rating-sheet:update`, `billing:usage-record:create` |
| networking (2) | `networking:qos-policy:delete`, `networking:subnet-pool:delete` |
| object-store (2) | `object-store:bucket-type:create`, `object-store:bucket-type:delete` |
| telemetry (2) | `telemetry:metric:create`, `telemetry:alarm:evaluate` |
| ha (1) | `ha:host:reset` |

`compute:aggregate:action` is one action for the three sub-actions of its dispatcher route (`add_host`, `remove_host`, `set_metadata`); splitting would change nothing today. The exact route of every action is listed in `docs/policy.md` (see Documentation).

## The engine

Three additions to `services/shared/policy.py`; nothing else in the engine changes.

1. **The table.** `DEFAULT_RULES` gains the 35 names, 57 in all. It becomes a read-only `MappingProxyType`, so no caller can add or change an action at runtime.
2. **`check(action, token)`.** For a site that cannot be a route dependency: it raises `HTTPException(403, "Policy does not allow <action>")` when `authorize` says no, and returns nothing otherwise. It raises `KeyError` for an unknown action, like `authorize`.
3. **`require(...)` marks its dependency.** The returned function carries `.action` (the action name), so a test can read which action a route is gated by without calling it. `require_auth` stays the default `authenticate`.

The 403 text of the converted routes changes from `admin role required` to `Policy does not allow <action>`, as it did for auth-api. A grep of the tests, the CLI and the dashboard found nothing that matches the old text.

## Converting a site

The rule that makes this safe: **the parameter keeps its position and name, only its annotation changes.** Handlers and tests call some gated functions as plain Python with the admin slot filled by position (`force_detach_volume("vol1", {"role": "admin"}, db)`, `set_inventories(name, payload, None, db)`, `create_rp` calling `create_rp_internal`). Moving the gate into `dependencies=[...]` and deleting the parameter would shift those arguments.

- `_: AdminUser` becomes `_: Annotated[dict, Depends(policy.require("service:resource:verb"))]`. Each service module may bind a small alias if it reads better; the action string appears once per route.
- `AdminOrInternal` routes (`compute:service:update`, `compute:server:evacuate`) use `policy.require(action, authenticate=require_user_or_internal)`. That function looks the test override of `require_auth` up by hand and accepts the internal token, which the HA monitor sends; `require_auth` alone would reject it.
- **Inline sub-actions** (`block-store:volume:force-delete` and `:reset-status`, inside `POST /volumes/{ref}/action`) call `policy.check(action, current)` at the place of today's test, after `require_volume`. The order of answers stays: a member on another project's volume gets 404, on their own volume 403.
- The old `AdminUser` and `AdminOrInternal` aliases stay exported: the sites that remain in code and the other services still use them.

## Hazards found by the inventory, and what handles each

| Hazard | Handling |
|---|---|
| Six tests assert the table is exactly the 22 identity actions (`test_policy.py`, `test_policy_parity.py`, `test_policy_cli.py` x3, `test_policy_endpoints.py`, `test_policy_routes.py`) | Updated to 57 and to the right prefix; the auth route-coverage assertion is limited to `identity:`. |
| The sample file must equal `policy defaults` byte for byte | Regenerated; the drift test stays. |
| `required_actions()` does not see inline `check()` calls | A coverage test imports every service and asserts `required_actions() ∪ INLINE == set(DEFAULT_RULES)`, with `INLINE` the two block-store names, and a route test drives each of them. |
| Policy state is process-global and a developer's `/etc/cloudx/policy.yaml` would change every service test | An autouse fixture in `tests/conftest.py` points `CLOUDX_POLICY_FILE` at an absent path and calls `policy.reset()`. |
| HA recovery uses the internal token on `compute:service:update` and `compute:server:evacuate`; a rule like `"!"` or `role:operator` breaks it | Documented as a lock-out the engine does not prevent; `policy check` lists every rule that differs from the default. |
| `compute:quota:set` forwards to auth-api `PUT /projects/{p}/quota`, which is `identity:project:quota-set`: two actions govern one operation | Documented: both must allow. |
| Placement has a second door for the same operations (`/internal/...`, `InternalAuth`) | Documented: the file governs the public route only. |
| `list_volume_types(db: DB, _: CurrentUser = None)` has a default on an annotated dependency | Not converted (it is `CurrentUser`); a test confirms it still runs after the neighbouring routes change. |

## Tests

- **Parity**, one test per service, over the five identities {system admin, domain admin, member, reader, internal}: the decision of the converted route with built-in rules equals the legacy gate, which the test spells out independently (`role == "admin"`).
- **Routes**, on each real app: 403 for a member and not 403 for an admin; the internal token passes the two `AdminOrInternal` routes; `.action` of each route's dependency is the expected name (this pins the 35 route-to-action mappings).
- **Inline sub-actions**: member 403 and admin 200 on `os-force_delete` and `os-reset_status`; the 404-before-403 order.
- **File effects** on at least two services: a rule opens one action to `role:member`; `"!"` closes one for an admin; a changed `mtime` is picked up.
- **Positional callers**: the existing direct calls (`force_detach_volume`, `set_inventories`) keep passing.
- **Engine**: `check()` allows and denies and raises `KeyError`; `.action` is set; `DEFAULT_RULES` is read-only.
- **Whole table**: 57 names, 35 new unique, no overlap with `identity:*`, pattern `service:resource:verb`.
- The full suite stays green; CI lint (`ruff` F401,F811,F821,F841) is run before each push.

## Documentation and packaging

- `docs/policy.md`: "22" becomes 57; the opening says the file now covers every service's admin-only actions; a section per service lists its actions with route and built-in rule; the limits section says what stays in code (ownership, visibility, state checks with an admin bypass, internal routes) and lists the lock-out cases above (HA token, `compute:quota:set`, the placement second door).
- `docs/feature-comparison.md` and `CHANGELOG.md` (Added/Changed: the 403 text).
- `etc/cloudx/policy.yaml.sample` regenerated from `cloudx-manage policy defaults`; the package keeps shipping it in `cloudx-common` and never writes `/etc/cloudx/policy.yaml`.
- Operators: the file must now exist, identical, on every node that runs any API service (until now only auth-api nodes).

## Task order (for the plan)

1. Engine: the 35 names, `MappingProxyType`, `check()`, `.action`; the global-fixture; the six existing assertions updated.
2. compute (12 routes, two with `require_user_or_internal`).
3. block-store (6 routes + 2 inline).
4. placement (5, positional callers kept).
5. billing + telemetry (5).
6. networking + object-store + ha (5).
7. Docs, changelog, sample, whole-table and coverage tests.
8. Full verification (suite, lint, `.deb` build).

Tasks 2 to 6 follow one pattern; each ends with its parity and route tests.

## Out of scope

Mixed sites; the 97 of them stay in code. `InternalAuth` routes. The authn-only routes listed in "Decisions". Per-project policy. A runtime API to edit rules. Domain-admin variants of these actions (the built-in rules ignore `domain_admin`, as the gates did; an operator can add `or domain_admin:True`).

## Risks

- A converted route silently changes behaviour. The parity tests, the `.action` pins and the unchanged positional callers are what shows it did not.
- An operator locks admins, or HA recovery, out with a rule. Not prevented, by decision; `policy check` and the docs are the guard rails.
- A policy file present on one node and not another gives different answers per node. Documented, as in PR 1.
