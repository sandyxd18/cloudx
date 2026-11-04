# Image read access: a non-public image is read by those who may read it

Date: 2026-11-04 · Delivery: PR 1 of 2 (image-api and the two workers) · Base: main after #115 · Follows: #112 (who may change an image)

## Goal

`GET /images/{ref}`, `/images/{ref}/download` and `/images/{ref}/tags` answer anybody, signed in or not, for any image, whatever its `visibility`. After this change an image that is not public is read only by those who may read it. A public image is still readable without signing in.

PR 2 (separate spec, after this one is merged) makes compute and block-store check that the caller may read an image before they boot from it, rebuild or rescue with it, or make a volume from it. They call image-api with the internal token today, so this PR alone does not stop a member of one project from booting a server from another project's private image by naming it.

## Decisions taken (with the project owner)

- **Public stays anonymous.** An image with `visibility == "public"` can be read without a token. Everything else needs an identity that passes the rule below.
- **Two PRs.** This one covers image-api and the workers that read images from it; PR 2 covers the use of an image by compute and block-store.

## The rule

`can_read_image(current, image)` in `services/image/app/main.py` is true when any of these holds:

1. `image.visibility == "public"`.
2. `owns(current, image.project_id)` (`shared.domain`): the owner project, an admin of the owner's domain, a system admin, or the internal token (synthetic admin). An image without an owner is therefore read by system admins and the internal token only.
3. The caller's project has an `ImageMember` row for the image with `status == "accepted"`. A `pending` or `rejected` row grants nothing.

`current` is `None` for an anonymous caller: only the first condition can hold.

**Denial.** An image that the caller may not read answers `404 image '<ref>' not found`, as an image that does not exist does, so its existence is not disclosed. For an anonymous caller, image-api also logs one WARNING per refused request: `anonymous read of a non-public image refused: <ref>`, so that an operator can find a worker that does not send the internal token (see Upgrade).

**Name lookup.** `get_image(db, ref)` takes the first image whose name matches, whoever owns it. With the rule above that could answer 404 to an owner who has an image of the same name as another project's. A new `find_readable_image(db, ref, current)` resolves `ref` like this: by id first (the rule applies to it); otherwise among the images named `ref` that the caller may read, the caller's own project first, then public, then the rest, ordered by `created_at`. `require_image` and the write paths (`require_owned_image`) keep using `get_image`; nothing about changing an image moves.

## Routes

| Route | Today | After |
|---|---|---|
| `GET /images/{ref}` | anonymous | `OptionalUserOrInternal`, rule above |
| `GET /images/{ref}/download` | anonymous | same |
| `GET /images/{ref}/tags` | anonymous | same |
| `GET /images/{ref}/verify` | any signed-in user | same rule (signed in or not no longer matters for public) |
| `GET /images/{ref}/members` | any signed-in user | same rule |
| `POST /images/{ref}/prefetch` | any signed-in user | signed in, and the rule |

Not changed: `GET /images` (its filter is the list rule), `POST /images/import-path` and `PATCH /v2/tasks/{id}` (internal), `GET /v2/tasks`, every write route (#112), `/health`.

## The dependency

`OptionalUserOrInternal` in `services/shared/auth_middleware.py`: the internal token when `X-Internal-Token` matches `INTERNAL_TOKEN` (the same synthetic admin claims as `require_user_or_internal`), otherwise the user's JWT through `require_auth` when it is valid, otherwise `None`. A bad, expired or revoked token makes the caller anonymous; it is not a 401. It honours the test override of `require_auth` the way `require_user_or_internal` does.

## The workers

`compute_worker` (`worker.py` `fetch_image`, `image_manager.py` `fetch_image_meta` and `download_image`) and `block_store_worker` (`main.py` `download_image`, both requests) add `headers=internal_headers()` to their reads from image-api. Nothing else in the workers reads an image. compute-api already sends `internal_headers()` (`clients.api_get`) and does not change here.

## Upgrade

A new image-api with an old worker: the worker reads public images as before; a non-public image answers 404 (and image-api logs the WARNING) until that node's worker is upgraded. A new worker with an old image-api: the extra header is ignored. So no order breaks a running cloud; the release notes say that private images on a node wait for its worker. A cloud without `INTERNAL_TOKEN` set on the workers and on image-api cannot read non-public images from the workers; the `.deb` packages generate `internal_token` on first install (`docs/deb-install.md` section 4) and `/etc/cloudx/cloudx.conf` is the same file on every node.

## Tests

`tests/image/test_read_access.py`, in the style of `tests/image/test_ownership.py`:

- Matrix: identities {owner, other project, domain admin of the owner's domain, domain admin of another domain, system admin, internal token, accepted member, pending member, rejected member, anonymous, anonymous with a bad token} × routes {show, download, tags, verify, members, prefetch} × visibility {public, private}: allowed or 404, and the right body for an allowed call.
- An image without an owner: system admin and internal token read it, a member does not.
- The anonymous refusal logs the WARNING once per request, and a refusal for a signed-in caller does not.
- Name lookup: two projects each own an image called `base`; each owner gets their own; a third project asking for `base` gets the public one or 404.
- `OptionalUserOrInternal`: internal token, valid JWT, no token, bad token, and the `require_auth` override (in `tests/shared`).
- Workers: `tests/compute_worker` and `tests/block_store_worker` capture the headers of the three and two requests and expect `X-Internal-Token` when `INTERNAL_TOKEN` is set.
- The existing image tests (`test_ownership`, `test_glance_fields`, `test_prefetch`, `test_upload_for_project`) stay green; any that read an image anonymously are updated and the change is explained in the commit.

## Documentation

CHANGELOG `### Fixed` (no detail of how the gap was reached; "upgrade soon", as for #112). The release notes of the next candidate describe the upgrade window above. No other doc describes anonymous image reads.

## Out of scope

Compute and block-store checks on use (PR 2). Making the default `visibility` of a form upload `private` (it is `public` today; a separate decision). Listing images shared with a project in `GET /images` (the list ignores shares today). Restricting `members` to the owner. The cache (`_cache_get` is a no-op).

## Risks

- Public images remain anonymous and the form upload defaults to `public`: an owner who leaves the default exposes the file. Recorded, not changed here.
- `members` of a public image lists project ids to anyone who can read the image.
- Until PR 2, a member can still boot from another project's private image through compute.
