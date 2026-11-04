# Image use check: compute and block-store ask whether the caller may read the image

Date: 2026-11-04 · Delivery: PR 2 of 2 (compute, block-store, and the image-api side) · Base: main after #116 · Follows: `2026-11-04-image-read-access-design.md`

## Goal

A member of one project can still boot a server from, rebuild or rescue a server with, or create a volume from another project's private image by naming it. compute-api and block-store-api look the image up in image-api with the internal token, which image-api reads as an admin, and `POST /volumes` does not look at the image at all. Since #116 the block-store worker reads any image with the same token, so the volume gets built.

After this change, those four operations succeed only for an image the caller may read (public, owned, or accepted share), with the same rule as image-api's own read routes. Without the right the caller gets `404 image '<ref>' not found`, as for a missing image.

## Decisions taken (with the project owner)

- **Claims on behalf of the caller.** The service calls image-api with the internal token plus a header that carries the validated claims of its caller; image-api applies its one `can_read_image` rule to them. The alternative, forwarding the caller's own credentials, was declined: it needs the raw request in every handler and a second token validation.
- **One rule.** The rule stays in image-api; compute and block-store copy none of it.

## The mechanism

**Header.** `X-Caller-Claims`: a JSON object, `json.dumps(..., separators=(",", ":"))` (ASCII, non-ASCII escaped), at most 2048 bytes. Keys kept: `sub`, `username`, `project_id`, `role`, `roles`, `domain_admin`, `domain_id`, `scope_type`; any other key is dropped.

**Receiver.** `optional_user_or_internal` (`services/shared/auth_middleware.py`, from #116) changes only for a request whose `X-Internal-Token` is valid:
- the header is absent: the synthetic admin, as today;
- the header is present and valid: the claims in it are the caller's identity;
- the header is present and not valid (not JSON, not an object, over 2048 bytes, `role` missing or not a string, `project_id` present and not a string): **anonymous (`None`)**. It never falls back to the synthetic admin.

Without a valid internal token the header is ignored: the caller is whatever the JWT path gives (user or anonymous). Only the three routes that use `optional_user_or_internal` (`GET /images/{ref}`, `/download`, `/tags`) read the header.

**Sender.** `on_behalf_headers(current: dict | None) -> dict` in `services/shared/internal.py`: `internal_headers()` plus `X-Caller-Claims` built from `current`. For `current is None` or the synthetic internal caller (`sub == "internal"`) it returns `internal_headers()` alone (admin, as today). When `INTERNAL_TOKEN` is unset it returns `{}`: the call is anonymous and sees public images only.

## The four operations

- **compute** `api_get(url, caller=None)` in `services/compute/app/clients.py` sends `on_behalf_headers(caller)` instead of `internal_headers()` when `caller` is given (the default keeps today's behaviour for every other lookup). `create_server` (`routers/servers.py`, the image lookup), `rebuild_server` and `rescue_server` (`routers/server_actions.py`) pass `caller=current`. The 404 of image-api reaches the user through the existing error path. Handler signatures do not change. The display lookup of an image name (`serializers.py`) and the name check of `create_server_snapshot` (an existence check) stay as they are.
- **block-store** `POST /volumes` (`create_volume`, `routers/volumes.py`): when `payload.image` or `payload.image_id` is set, look the image up in image-api with `on_behalf_headers(current)` before `_create_volume_record` creates anything; use the resolved `id` as `image_ref`. A catalog failure stays 502; an image-api answer of 404 is 404. `POST /internal/volumes` (compute's call, already checked) does not change.
- Callers that act with the internal token alone (HA, the workers, the orchestration worker) stay admin and are not affected.

## Upgrade

An old image-api ignores the header and reads the internal token as admin: no regression. Old compute and block-store never send it: unchanged. So any order is safe, and the check applies once all three are new. No schema change.

## Tests

- **image-api** (`tests/image/test_read_access.py` or a new `test_caller_claims.py`): with a valid internal token, claims for the owner, another project, an accepted member, a domain admin, an admin; each follows the rule on show, download and tags. Claims without the internal token, with a wrong token, and with `INTERNAL_TOKEN` unset are ignored. Malformed JSON, a list, an oversized header, `role` not a string: anonymous (public only). The header with a valid token but no claims: admin as before.
- **Round trip:** `on_behalf_headers(current)` fed into an image-api `TestClient` request for the same `current`: the answer equals the rule for that `current`, for each identity above. This pins that sender and receiver agree.
- **shared:** `on_behalf_headers` for a member, `None`, the synthetic internal caller, unset `INTERNAL_TOKEN`, a `current` with non-ASCII values (stays ASCII, under the limit).
- **compute:** create, rebuild and rescue refuse an image the caller cannot read with 404 and leave no server, no volume cast and no state change; they accept a public, an owned and a shared image; admin accepts any; the request to image-api carries the claims header. Existing tests that stub `clients.api_get` take `**kw`.
- **block-store:** `POST /volumes` with an unreadable image answers 404 and creates no record; with a readable image the record carries the resolved id; without an image nothing calls image-api.
- The existing compute, block-store, image and shared tests stay green.

## Documentation

CHANGELOG `### Fixed` (no detail of how the gap was reached; "Upgrade soon"). A short paragraph in the notes of the next candidate describing the claims header as a trust of the internal token.

## Out of scope

Default `visibility` of a form upload; the 409 name oracle; listing images shared with a project; the other `hmac.compare_digest(str, str)` sites; PR 1's remaining minor items.

## Risks

- A holder of the internal token can state any identity to image-api. The token already grants admin on every service, so no right is added, but it is a new trust and the notes say so.
- With `INTERNAL_TOKEN` unset, compute and block-store cannot read non-public images (the packages always set it).
- An image used by a server before this change stays usable by that server (rebuild with the same image by someone who cannot read it is refused; the stored `image_id` is not re-checked).
