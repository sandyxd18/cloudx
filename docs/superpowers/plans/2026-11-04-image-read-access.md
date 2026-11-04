# Image read access Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A non-public image is read only by those who may read it; a public image stays readable without signing in; the two workers that read images send the internal token.

**Architecture:** A new shared dependency yields the caller's identity or `None`. image-api gets one rule function and a name lookup restricted to readable images, and the read routes use them. The compute and block-store workers add `internal_headers()` to their reads.

**Tech Stack:** Python, FastAPI, SQLAlchemy, pytest.

**Spec:** `docs/superpowers/specs/2026-11-04-image-read-access-design.md` (authority when this plan is silent or wrong).

## Global Constraints

- Rule `can_read_image` is true when: `image.visibility == "public"`; or `owns(current, image.project_id)` (`shared.domain`; false when `current` is `None`); or the caller's project has an `ImageMember` row for the image with `status == "accepted"` (`pending` and `rejected` grant nothing). The caller's project is `current.get("project_id")`: a token without one (system scope) must not raise.
- A refusal answers `404` with `{"detail": "image '<ref>' not found"}`, the same body as a missing image. For an anonymous caller (`current is None`) whose request is refused because the image exists but is not readable, image-api logs one WARNING per request: `anonymous read of a non-public image refused: <ref>`. A signed-in refusal logs nothing.
- Name lookup `find_readable_image(db, ref, current)`: by id first (the rule applies); otherwise, among images named `ref` that the caller may read, the caller's own project first, then public, then the rest, ordered by `created_at`. `get_image`, `require_image` and `require_owned_image` do not change; the write routes are not touched.
- `OptionalUserOrInternal` (in `services/shared/auth_middleware.py`): the internal token (`X-Internal-Token` equal to `INTERNAL_TOKEN`) gives the same synthetic admin claims as `require_user_or_internal`; otherwise a valid user JWT through `require_auth`; otherwise `None`. A bad, expired or revoked token gives `None`, never 401. It honours the test override of `require_auth` as `require_user_or_internal` does (implement it by calling `require_user_or_internal(request)` and turning an `HTTPException` into `None`).
- Routes: `GET /images/{ref}`, `GET /images/{ref}/download`, `GET /images/{ref}/tags` use `OptionalUserOrInternal` (anonymous allowed for public). `GET /images/{ref}/verify`, `GET /images/{ref}/members` and `POST /images/{ref}/prefetch` keep `CurrentUser` (signed in) and add the rule. Ruling (spec table is silent on whether `verify`/`members` open to anonymous): they stay signed-in-only, least change.
- Not changed: `GET /images` list, `POST /images/import-path`, `PATCH /v2/tasks/{id}`, `GET /v2/tasks`, every write route, `/health`, compute-api's own calls, the default `visibility` of an upload.
- `compute_worker` (`worker.py` `fetch_image`; `image_manager.py` `fetch_image_meta` and `download_image`) and `block_store_worker` (`main.py` `download_image`, both requests) pass `headers=internal_headers()` (`from shared.internal import internal_headers`).
- Imports as in the repo (`from shared...`). Run the CI lint before every push: `ruff check services cli tests --select F401,F811,F821,F841 --exclude "node_modules,migrations/versions"`. Python is `.venv-dev/bin/python`.
- Commit trailers on every commit: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_015ZZ3VowHusnEAJzUhJtVob`.

## Review Focus

1. **A public image is still downloaded without any header** (old compute workers during the upgrade), including through `FileResponse` and the minio stream: Task 2 tests it.
2. **Two projects with an image of the same name**: each owner gets their own; a stranger gets the public one or 404: Task 2.
3. **A bad or expired token on a public image** reads it as anonymous (200), and on a private image answers 404, not 401: Tasks 1 and 2.
4. **An image without an owner** (`project_id` is `None`) and **a system-scoped token without `project_id`**: no crash, only system admin and the internal token read the first: Task 2.
5. **The workers send the header only when `INTERNAL_TOKEN` is set** and do not break when it is unset (`internal_headers()` is empty then): Task 3.

---

### Task 1: `OptionalUserOrInternal`

**Files:**
- Modify: `services/shared/auth_middleware.py`
- Test: `tests/shared/test_optional_user_or_internal.py`

**Interfaces:**
- Produces: `optional_user_or_internal(request: Request) -> dict | None` and the alias `OptionalUserOrInternal = Annotated[dict | None, Depends(optional_user_or_internal)]`, both exported from `shared.auth_middleware`.
- Consumes: `require_user_or_internal` from the same module.

- [ ] **Step 1: Write the failing tests** in `tests/shared/test_optional_user_or_internal.py` on a throwaway FastAPI app with one route `GET /who` returning `{"who": current}`: `test_internal_token_gives_the_synthetic_admin` (`INTERNAL_TOKEN` set, header matches: `sub == "internal"`, `role == "admin"`); `test_a_wrong_internal_token_is_anonymous` (200, `who` is null); `test_a_valid_user_token_gives_its_claims` (sign a real JWT as `tests/billing` does, or override `require_auth`; claims returned); `test_no_token_is_anonymous`; `test_a_garbage_or_expired_token_is_anonymous_not_401`; `test_the_require_auth_override_is_honoured` (override returning a dict gives that dict; an override raising `HTTPException(401)` gives null); `test_without_internal_token_configured_a_header_does_not_open_anything` (`INTERNAL_TOKEN` unset, any `X-Internal-Token` header: anonymous).
- [ ] **Step 2: Run them; expect failures** (`.venv-dev/bin/python -m pytest tests/shared/test_optional_user_or_internal.py -q`).
- [ ] **Step 3: Implement** `optional_user_or_internal` and the alias in `services/shared/auth_middleware.py`, next to `optional_auth`.
- [ ] **Step 4: Run the new file and `tests/shared -q`; run the CI lint; expect green.**
- [ ] **Step 5: Commit** `feat(shared): OptionalUserOrInternal, the caller's identity or none`.

---

### Task 2: image-api read rule, routes, CHANGELOG

**Files:**
- Modify: `services/image/app/main.py`, `CHANGELOG.md`
- Test: `tests/image/test_read_access.py`

**Interfaces:**
- Consumes: `OptionalUserOrInternal` (Task 1); `owns` from `shared.domain`.
- Produces, in `services/image/app/main.py`:
  - `can_read_image(db: Session, current: dict | None, image) -> bool`;
  - `find_readable_image(db: Session, ref: str, current: dict | None)` returning the image or `None`;
  - `require_readable_image(db: Session, ref: str, current: dict | None)` returning the image or raising the 404 (and logging the anonymous WARNING) as in Global Constraints.

- [ ] **Step 1: Write the failing tests** in `tests/image/test_read_access.py`, in the style of `tests/image/test_ownership.py` (reload the module, `who` dict behind overrides of `require_auth` and `require_user_or_internal`; an `ANON` identity is an override that raises `HTTPException(401)`; images are made through `POST /images` with form `visibility=public|private`). Identities: OWNER (p1), OTHER (p2), DOMAIN_ADMIN of p1's domain, DOMAIN_ADMIN of another domain, ADMIN, INTERNAL, ACCEPTED_MEMBER (p3 with an `accepted` row), PENDING_MEMBER (p4 `pending`), REJECTED_MEMBER (p5 `rejected`), ANON. Tests:
  - `test_the_matrix_of_identities_routes_and_visibility`: parametrized over identities × routes {`GET /images/victim`, `/download`, `/tags`, `/verify`, `/members`} × visibility {public, private}; expected: allowed (200, with the right body: the image dict, the file bytes `b"hello"`, `{"tags": [...]}`) or 404 `{"detail": "image 'victim' not found"}`. `/verify` and `/members` for ANON expect 401 (still signed-in-only) on both visibilities; the rule is tested for the signed-in identities.
  - `test_prefetch_follows_the_rule` (`POST /images/victim/prefetch`: OWNER ok on private, OTHER 404 on private, OTHER ok on public).
  - `test_an_image_without_an_owner` (set `project_id = None` in the row): ADMIN and INTERNAL read it, OWNER/OTHER get 404, ANON gets 404 unless public.
  - `test_a_token_without_a_project_does_not_crash` (identity `{"role": "member", "sub": "s", "scope_type": "system"}` with no `project_id`: 404 on private, 200 on public).
  - `test_a_public_image_downloads_with_no_credentials_at_all` (ANON, file backend, bytes equal; and the minio branch with a stubbed `MinioStorageBackend` if the existing tests already stub it, otherwise skip that branch and say so).
  - `test_anonymous_refusal_logs_one_warning_and_a_signed_in_refusal_logs_none` (`caplog`).
  - `test_name_lookup_prefers_the_callers_own_image`: p1 and p2 each own an image called `base`; OWNER and OTHER each get their own id back from `GET /images/base`; a third project gets 404 when both are private and the public one when one is public.
  - `test_lookup_by_id_applies_the_rule`.
- [ ] **Step 2: Run them; expect failures.**
- [ ] **Step 3: Implement** `can_read_image`, `find_readable_image`, `require_readable_image` and switch the six routes as in Global Constraints (`show_image`, `download_image`, `list_image_tags` to `OptionalUserOrInternal`; `verify_image`, `list_image_members`, `prefetch_image` keep their `CurrentUser` and use `require_readable_image`). The route bodies otherwise do not change. Existing image tests that read an image anonymously keep passing (they override the auth dependencies); update any that cannot and say why in the commit message.
- [ ] **Step 4: Add the CHANGELOG entry** under `### Fixed` of `## Unreleased` (create the `## Unreleased` heading above `## 1.0.10-rc2` with a `### Fixed` subsection if it is empty): reading a non-public image needs a login and the right to read it; a public image is still readable without signing in; the workers send the internal token; a non-public image answers 404 and a WARNING is logged until a node's worker is upgraded; "Upgrade soon". No detail of how the gap was reached.
- [ ] **Step 5: Run** `.venv-dev/bin/python -m pytest tests/image tests/shared -q` and the CI lint; expect green.
- [ ] **Step 6: Commit** `fix(image): a non-public image is read by those who may read it`.

---

### Task 3: the workers send the internal token

**Files:**
- Modify: `services/compute_worker/app/worker.py`, `services/compute_worker/app/image_manager.py`, `services/block_store_worker/app/main.py`
- Test: `tests/compute_worker/test_image_read_headers.py`, `tests/block_store_worker/test_image_read_headers.py`

**Interfaces:** Consumes `internal_headers` from `shared.internal` (already imported in `compute_worker/app/main.py` and `block_store_worker/app/main.py`; add the import where missing). Produces nothing for other tasks.

- [ ] **Step 1: Write the failing tests** (follow how `tests/compute_worker/test_compute_worker_catalog.py` and `tests/block_store_worker/test_image_url_catalog.py` stub `requests.get`): `test_fetch_image_sends_the_internal_token`, `test_fetch_image_meta_and_download_image_send_the_internal_token` (compute worker; the download test stubs a streaming response), `test_block_store_download_image_sends_the_token_on_both_requests`; each also has a case with `INTERNAL_TOKEN` unset where the request carries no `X-Internal-Token` and nothing raises.
- [ ] **Step 2: Run them; expect failures.**
- [ ] **Step 3: Add `headers=internal_headers()`** to the five `requests.get` calls named in Global Constraints. Nothing else changes.
- [ ] **Step 4: Run** `.venv-dev/bin/python -m pytest tests/compute_worker tests/block_store_worker -q` and the CI lint; expect green.
- [ ] **Step 5: Commit** `fix(workers): read images from image-api with the internal token`.
