# Image use check Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Creating a server from, rebuilding or rescuing a server with, and creating a volume from an image succeed only for an image the caller may read, using image-api's own rule.

**Architecture:** compute and block-store call image-api with the internal token plus `X-Caller-Claims`, the validated claims of their caller. image-api's `optional_user_or_internal` uses those claims as the identity when the internal token is valid, and its existing `can_read_image` decides. Rule stays in one place.

**Tech Stack:** Python, FastAPI, pytest.

**Spec:** `docs/superpowers/specs/2026-11-04-image-use-check-design.md` (authority when this plan is silent or wrong; it follows `2026-11-04-image-read-access-design.md`, already merged as #116).

## Global Constraints

- Header `X-Caller-Claims`: `json.dumps(claims, separators=(",", ":"))` (ASCII), at most 2048 bytes. Keys kept: `sub`, `username`, `project_id`, `role`, `roles`, `domain_admin`, `domain_id`, `scope_type`; any other key is dropped. Types: `role` a non-empty `str` (required); `project_id`, `domain_id`, `sub`, `username`, `scope_type` `str` or absent/`None`; `roles` a `list` of `str`; `domain_admin` a `bool`. Anything else makes the header invalid.
- Receiver (`optional_user_or_internal` in `services/shared/auth_middleware.py`): only when `INTERNAL_TOKEN` is set and `X-Internal-Token` matches it (use the existing `_internal_token_matches`): header absent gives the synthetic admin as today; header present and valid gives its claims; header present and invalid (not JSON, not an object, over 2048 bytes, wrong types, `role` missing) gives `None` (anonymous), **never** the synthetic admin. Without a valid internal token the header is ignored and the JWT path decides as today. Only the routes that use `optional_user_or_internal` read it.
- Sender: `on_behalf_headers(current: dict | None) -> dict` in `services/shared/internal.py`: `internal_headers()` plus `X-Caller-Claims` built from `current`. For `current is None` or `current.get("sub") == "internal"` it returns `internal_headers()` alone. When `INTERNAL_TOKEN` is unset it returns `{}`.
- compute: `clients.api_get(url, caller=None)` in `services/compute/app/clients.py` sends `on_behalf_headers(caller)` instead of `internal_headers()` when `caller` is given; every other call is unchanged. `create_server`, `rebuild_server`, `rescue_server` pass `caller=current` for their image lookup. Handler signatures do not change. Not changed: the display lookup in `serializers.py`, the name check of `create_server_snapshot`.
- block-store: `create_volume` (`POST /volumes`, `routers/volumes.py`) checks the image with `on_behalf_headers(current)` before `_create_volume_record` creates anything, when `payload.image` or `payload.image_id` is set, and uses the resolved `id` as `image_ref`. A catalog failure stays 502; image-api's 404 is 404. `POST /internal/volumes` is unchanged.
- A refusal is `404 image '<ref>' not found` (image-api's body, passed through).
- Run the CI lint before every push: `.venv-dev/bin/ruff check services cli tests --select F401,F811,F821,F841 --exclude "node_modules,migrations/versions"`. Python is `.venv-dev/bin/python`.
- Commit trailers on every commit: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_015ZZ3VowHusnEAJzUhJtVob`. Stage only files you changed; never `.superpowers/`.

## Review Focus

1. **A claims header can never raise an identity**: with a missing, wrong or empty internal token it is ignored; with a valid token and an invalid header the caller is anonymous, not admin: Task 1.
2. **A refused image leaves nothing behind in `create_server`**: the image lookup runs after `claim_host`; the placement claim must be released and no server row, root volume or cast must exist: Task 2.
3. **Internal callers stay admin**: a `current` whose `sub` is `internal` (HA, the orchestration worker) must still read any image: Tasks 1 and 2.
4. **`POST /volumes` creates no record when the image is refused**, and quota counters do not move: Task 3.
5. **Non-ASCII in claims** (a username with an accent) stays ASCII in the header and does not break the request: Task 1.

---

### Task 1: the claims header, sender and receiver

**Files:**
- Modify: `services/shared/internal.py`, `services/shared/auth_middleware.py`
- Test: `tests/shared/test_caller_claims.py`, `tests/image/test_caller_claims.py`

**Interfaces:**
- Produces: `on_behalf_headers(current: dict | None) -> dict` (`shared.internal`); the extended `optional_user_or_internal`/`OptionalUserOrInternal` (`shared.auth_middleware`).
- Consumes: `_internal_token_matches` and `optional_user_or_internal` from #116; image-api's `can_read_image` rule (unchanged).

- [ ] **Step 1: Write the failing tests.** `tests/shared/test_caller_claims.py` on a throwaway FastAPI app with one `OptionalUserOrInternal` route echoing the identity: sender cases (`on_behalf_headers` for a member, a domain admin, `None`, the synthetic internal caller, unset `INTERNAL_TOKEN` gives `{}`, a username `"josé"` stays ASCII, extra keys dropped, a claims dict that would exceed 2048 bytes: raise or truncate the `roles` list? Rule: if over the limit the sender raises `ValueError`; the test pins it); receiver cases (valid token plus valid claims gives exactly those claims minus dropped keys; valid token and no header gives the synthetic admin; valid token and each invalid header form gives `None`; no token, wrong token, empty token, unset `INTERNAL_TOKEN` each with a valid claims header give the JWT result, anonymous when there is no JWT, and never the claims). `tests/image/test_caller_claims.py` in the style of `tests/image/test_read_access.py`: with the real image app (no `require_auth` override for the call under test, `INTERNAL_TOKEN` set) and headers from `on_behalf_headers(current)`: for owner, other project, accepted member, pending member, domain admin of the owner's domain, admin, a `current` with no project: `GET /images/{ref}`, `/download`, `/tags` on a public and a private image answer exactly as the rule says (200 or the 404 body); a claims header with a wrong token reads as anonymous (private: 404); the internal token alone reads the private image.
- [ ] **Step 2: Run them; expect failures.**
- [ ] **Step 3: Implement** `on_behalf_headers` and the claims parsing/validation in the receiver as in Global Constraints (a small private helper `_caller_from_claims_header(raw: str) -> dict | None`).
- [ ] **Step 4: Run** `.venv-dev/bin/python -m pytest tests/shared tests/image -q` and the CI lint; expect green.
- [ ] **Step 5: Commit** `feat(shared): X-Caller-Claims, a service states its caller's identity to image-api`.

---

### Task 2: compute asks image-api as the caller

**Files:**
- Modify: `services/compute/app/clients.py`, `services/compute/app/routers/servers.py`, `services/compute/app/routers/server_actions.py`
- Test: `tests/compute/test_image_use_check.py`; update existing tests whose `clients.api_get` stub rejects a `caller=` keyword (make them accept `**kw`).

**Interfaces:**
- Consumes: `on_behalf_headers` (Task 1).
- Produces: `clients.api_get(url: str, caller: dict | None = None)`.

- [ ] **Step 1: Write the failing tests** in `tests/compute/test_image_use_check.py`. Wire `clients.api_get` for the image URL to a real image-api `TestClient` through an adapter that turns the call into a request carrying the headers the code under test built (so compute and image-api agree end to end), and stub the other lookups as `tests/compute/test_server_create.py` does: `test_create_server_refuses_an_image_the_caller_cannot_read` (member of p2, private image of p1: 404 body `image 'x' not found`; no `Server` row, no placement allocation left (check what `claim_host`/the release path touches), no block-store call, no cast); `test_create_server_accepts_public_owned_and_shared_images`; `test_admin_and_the_internal_caller_read_any_image` (`sub: internal`); `test_rebuild_refuses_an_unreadable_image_and_changes_nothing` (status stays, no cast); `test_rescue_refuses_an_unreadable_image`; `test_rebuild_and_rescue_accept_a_readable_image`; `test_api_get_sends_the_claims_header_only_when_a_caller_is_given` (unit test of `clients.api_get` with `requests.get` stubbed: no `caller` gives `internal_headers()` only, as today).
- [ ] **Step 2: Run them; expect failures.**
- [ ] **Step 3: Implement** `api_get(url, caller=None)` and pass `caller=current` at the three image lookups. Update existing stubs that fail on the keyword.
- [ ] **Step 4: Run** `.venv-dev/bin/python -m pytest tests/compute tests/shared tests/image -q` and the CI lint; expect green.
- [ ] **Step 5: Commit** `fix(compute): booting from, rebuilding or rescuing with an image needs the right to read it`.

---

### Task 3: block-store `POST /volumes`, CHANGELOG

**Files:**
- Modify: `services/block_store/app/routers/volumes.py` (and `services/block_store/app/service.py` only if the lookup helper belongs there), `CHANGELOG.md`
- Test: `tests/block_store/test_image_use_check.py`

**Interfaces:** Consumes `on_behalf_headers` (Task 1).

- [ ] **Step 1: Write the failing tests** (wire the image lookup to a real image-api `TestClient` as in Task 2): `test_create_volume_refuses_an_unreadable_image` (404, no `Volume` row, no cast to the worker, quota usage unchanged); `test_create_volume_from_a_readable_image_uses_the_resolved_id` (call with the image name, the record's `image_id` is the id); `test_create_volume_without_an_image_does_not_call_image_api`; `test_the_internal_route_is_unchanged` (`POST /internal/volumes` with an unreadable image by a member-claims call is not checked: it is internal); `test_a_catalog_failure_is_still_502`; `test_admin_and_the_internal_caller_read_any_image`.
- [ ] **Step 2: Run them; expect failures.**
- [ ] **Step 3: Implement** the check in `create_volume` before `_create_volume_record`.
- [ ] **Step 4: CHANGELOG** `### Fixed` under `## Unreleased` (create the heading above `## 1.0.10-rc2` if absent): booting from, rebuilding or rescuing with, and creating a volume from an image need the right to read it (public, owned, or accepted share); compute and block-store now tell image-api who is asking with `X-Caller-Claims`, sent with the internal token; any upgrade order is safe, the check applies once image-api, compute and block-store are all new; with `INTERNAL_TOKEN` unset those services read public images only; "Upgrade soon". No detail of how the gap was reached.
- [ ] **Step 5: Run** `.venv-dev/bin/python -m pytest tests/block_store tests/compute tests/shared tests/image -q` and the CI lint; expect green.
- [ ] **Step 6: Commit** `fix(block-store): creating a volume from an image needs the right to read it`.
