# Changelog

All notable changes to CloudX. Each release from 0.7.0 on has full notes, including upgrade steps, in
`docs/releases/<tag>.md`; this file is the short version. Versions before 1.0.0 may change configuration and database
schema between releases, so read the upgrade steps before moving a running cloud.

## Unreleased

### Fixed
- **Volume and snapshot references are checked against the caller's project.** Booting from a volume, and cloning a
  volume or a snapshot, now require the volume or snapshot to belong to the caller (or the caller to be an admin or a
  service). Attaching a volume through `POST /volumes/{ref}/attach` also checks the target server's project. Compute's
  `POST /servers/{ref}/volumes/attach` and `/detach` now accept only an admin or the internal token, whoever owns the
  volume; users attach and detach through block-store's `POST /volumes/{ref}/attach` and `/detach`. The owner of an
  image made from a volume is the volume's project unless an admin or a service names another one; a domain admin no
  longer names it. **Upgrade soon.**
- **Networks, ports, security groups and server groups named by a user are checked against the caller's project.**
  Creating a server, and adding a port to one, now resolve the networks, ports and security groups the user names as
  that user: compute sends its caller's identity to networking with the internal token, so networking's own rules apply
  (own project, the admin of its domain, shared and external networks). Server groups named by id are checked like
  those named by name. Ports of new servers belong to the project that created the server and count against its port
  quota; servers that already exist do not change. A member cannot delete a port that a server uses (`409`). A port with
  no security group named gets the project's `default` group, else the installation's. A network that other projects
  boot on needs to be `shared` or `external`, and a domain admin names another project's network by id. Every
  user-or-internal route that uses the shared auth middleware now honours the identity header when one is sent.
  **Upgrade soon.**

## 1.0.10-rc3 (2026-11-04)

Release candidate, after 1.0.10-rc2 and not yet run on the lab: the same features, plus two image access fixes. Reading
a non-public image needs the right to read it (a public image stays readable without signing in), and booting from,
rebuilding or rescuing with, and creating a volume from an image need it too. **Upgrade soon.** Upgrade the compute
and block-store workers before or soon after the node that runs `image-api`: until a node's workers are new, a boot from
a private image on it answers `404`. No schema change beyond rc1's three. The notes, with the upgrade steps and what was
and was not tested, are in `docs/releases/v1.0.10.md`.

### Fixed
- **Reading a non-public image needs a login and the right to read it.** On `image-api`, `GET /images/{image}`, its
  `download`, `tags`, `verify`, `members` and `prefetch` now answer only to the image's owner, an admin of the owner's
  domain, a system admin, the internal token, and a project the image was shared with that accepted it (a pending or
  rejected share grants nothing). Anyone else gets `404 image '<image>' not found`, the same answer as for a missing
  image. A public image is still readable, and downloadable, without signing in. `verify`, `members` and `prefetch`
  still need a login (the internal token is not accepted there, and nothing calls them with it). `GET /images` with a
  `visibility` filter now also applies the project filter. The compute and block-store workers now send the internal
  token when they read an image. Upgrade the compute and block-store nodes before the node that runs `image-api`. Until a node's workers are upgraded, booting
  from, rebuilding or rescuing with, or creating a volume from a private image (server snapshots, volume uploads and
  task imports are private by default) fails with `404` on that node; public images keep working. `image-api` logs
  `anonymous read of a non-public image refused: <ref>` for each refused read. Upgrade soon.
- **Booting from, rebuilding or rescuing with, and creating a volume from an image need the right to read it.** The
  image must be public, owned by the caller's project or its domain's admin, a system admin, or shared with it and
  accepted; otherwise the request fails with `404 image '<image>' not found` before anything is created. `compute`
  and `block-store` now tell `image-api` who is asking, with an `X-Caller-Claims` header sent together with the
  internal token, and `image-api` judges the read as that user. Any upgrade order is safe: the check applies once
  `image-api`, `compute` and `block-store` are all new. With `INTERNAL_TOKEN` unset those services read public images
  only. Upgrade soon.

## 1.0.10-rc2 (2026-11-04)

Release candidate, after 1.0.10-rc1 and not yet run on the lab: the same features, plus a policy file for the
admin-only actions of every service (57 actions), and two access fixes: who may change an image (**upgrade soon**) and
who may call the four bulk billing and telemetry routes. No schema change beyond rc1's three. The notes, with the
upgrade steps and what was and was not tested, are in `docs/releases/v1.0.10.md`.

### Added
- **A policy file for auth-api.** `/etc/cloudx/policy.yaml` (mode 640, owner root, group cloudx; `CLOUDX_POLICY_FILE`
  names another path) overrides the rule of any of 22 `auth-api` actions: the domain, user, project and role assignment
  actions, the service and endpoint catalog, project and domain quotas, and the audit log. A rule is a boolean expression
  of `role:NAME`, `domain_admin:True|False`, `rule:NAME` (a named rule of the same file), `@`, `!`, `and`, `or`, `not` and
  parentheses (at most 200 tokens). The built-in rules are the old checks, so a cloud without the file behaves as
  before. The file is re-read within 2 seconds of a change, no restart; a bad rule or a bad file never stops a service
  (the action keeps its default, an unreadable file keeps the last good rules) and is logged once per change by the
  `cloudx-policy` logger. It has to exist on every node that runs an API service. `cloudx-manage policy check [FILE]`
  validates a file and lists the rules that differ from the defaults; `cloudx-manage policy defaults` prints the built-in
  rules. `cloudx-common` installs a sample with every line commented out as `/usr/share/cloudx/policy.yaml.sample`
  (`etc/cloudx/policy.yaml.sample` in the repository and the tarball); no package creates `/etc/cloudx/policy.yaml`.
  See `docs/policy.md`. Ownership and visibility rules stay in code.
- **The policy file covers the admin-only actions of every service.** 35 more actions follow `/etc/cloudx/policy.yaml`,
  57 in all: 12 of compute (flavors and extra specs, aggregates, quota, service update, migrate, evacuate), 8 of
  block-store (volume types, quota, force-detach, and the `os-force_delete` and `os-reset_status` volume actions),
  5 of placement, 3 of billing, 2 each of telemetry, networking and object-store, and `ha:host:reset`. Each has the
  built-in rule `role:admin`, the check the route had, so a cloud without the file behaves as before. The file now has to
  exist, identical, on every node that runs any API service, not only auth-api nodes. `GET /policy` and
  `POST /policy/enforce` answer for all 57 actions, and `cloudx-manage policy defaults` and the sample list them.
  Ownership, visibility and state checks, `/internal/...` routes and the four bulk billing and telemetry routes stay in
  code. A rule of `"!"`, or one without `role:admin`, on `compute:service:update` or `compute:server:evacuate` stops HA
  recovery, and `compute:quota:set` also needs `identity:project:quota-set` to allow; see `docs/policy.md`.

### Changed
- **Bulk usage and metric calls need an admin or the internal token.** `POST /billing/usage-records/batch`,
  `POST /billing/cleanup`, `POST /telemetry/metrics/batch` and `DELETE /telemetry/metrics/cleanup` have accepted any
  signed-in user since 0.7.18, when they were opened to the billing and telemetry workers; they now answer `403` to anyone
  who is not an admin. The workers call them with the internal token and are not affected.
- **A denied request on the converted auth-api routes answers `403` with `Policy does not allow <action>`** instead of
  `admin role required` or `admin or domain admin required`. Who is allowed does not change unless a policy file says so.
- **A denied request on the 35 newly converted routes answers `403` with `Policy does not allow <action>`** instead of
  `admin role required`. This covers the admin-only routes of compute, block-store, placement, billing, telemetry,
  networking, object-store and ha, and the `os-force_delete` and `os-reset_status` volume actions of block-store. Who is
  allowed does not change unless a policy file says so.
- **`POST /policy/enforce` checks the real rules.** It answers `400` for an unknown or non-string `action` (it answered
  `allowed: true`), and the 17 action names of the old table, which no route ever enforced, are gone. `GET /policy` now
  lists every action of the table (57, see the entry above) and has a new `sources` map (`default` or `file` for each action).

### Fixed
- **Changing an image needs its owner.** `PATCH`, `DELETE`, `deactivate`, `reactivate`, adding or removing a tag, and
  sharing or unsharing with a project on `image-api` now require the image's owner, an admin of the owner's domain, or a
  system admin (the internal token counts as one, so `image-worker` is unchanged); anyone else gets `403 not authorized to
  modify this image`. An image without an owner can be changed by a system admin only. `PATCH` accepts `status`, `size`,
  `checksum`, `object_name`, `filename` and `storage_backend` from an admin or the internal token only; an owner still
  edits `name`, `visibility`, `protected`, `min_ram_mb`, the formats and `properties`. The file store serves only files
  inside its folder (`400 invalid image object name` otherwise). Upgrade soon.

## 1.0.10-rc1 (2026-11-04)

Release candidate: it is installed on the lab before the final 1.0.10 is tagged. A resize that is confirmed
automatically, a browser console served by a noVNC proxy on every compute node, domain and system scoped tokens,
application credential rotation and trusts, three dashboard pages, and three access checks (application credential roles,
application credential lookup, a domain's consolidated invoice), in the compute service, the reaper, the compute worker,
auth, billing, the dashboard and the packaging. **Three schema changes** (`compute_0005`, `auth_0008`, `auth_0009`; run
`cloudx-manage db sync`). The browser console now needs the `novnc` package and port 6080 on every compute node (see
Changed). The notes, with the upgrade steps and what was and was not tested, are in `docs/releases/v1.0.10.md`. None of it
has been run on the lab yet.

### Added
- **Resizes are confirmed automatically.** `cloudx-reaper` confirms a server that has waited in `VERIFY_RESIZE` longer
  than `RESIZE_CONFIRM_WINDOW_SECONDS` (default 86400; `0` turns it off), through the public `confirmResize`. The reaper
  runs every `REAPER_INTERVAL` seconds (600), so the confirmation comes up to one interval after the window;
  `REAPER_DRY_RUN` only logs. compute-api has `GET /internal/resize-pending?seconds=N` for it, and the server record has
  a new column `resize_pending_since` (migration `compute_0005`). Servers already in `VERIFY_RESIZE` at the upgrade get
  a full window from the upgrade. A server that an older version left `ACTIVE` with a rollback snapshot is not swept.
- **Domain and system scoped tokens.** `POST /auth/tokens` takes `scope` with one of `{"project": ...}`,
  `{"domain": ...}` or `{"system": true}`. A domain token (domain admin of that domain, or admin) has no project; only
  auth-api and billing accept it, every other service answers 403 `project-scoped token required`. A system token needs
  the admin role. See `docs/identity.md`.
- **Application credential rotation:** `POST /v3/users/{user}/application_credentials/{cred}/rotate` with an optional
  `grace_seconds` (0 to 86400), during which the replaced secret still works, and `cloudx auth appcred rotate USER CRED
  [--grace-seconds N]` (migration `auth_0008`).
- **Trusts:** `POST|GET /v3/OS-TRUST/trusts`, `GET|DELETE /v3/OS-TRUST/trusts/{id}` and `POST /auth/tokens/trust`
  (migration `auth_0009`). A trustor delegates roles it holds in one project to another user; the trustee exchanges the
  trust for a token that acts as the trustor, which is read-only on auth-api. Each exchange is written to the audit log
  as `trust.token`. Revoking a trust does not revoke tokens already issued from it; they stay valid until they expire
  (`AUTH_TOKEN_EXPIRE_HOURS`), but never past the trust's own expiry (`expires_in` reports the real lifetime).
- Dashboard pages for Trunks, QoS policies and Subnet pools under Networking.

### Fixed
- **An application credential is reached through its own user only.** `GET` and `DELETE
  /v3/users/{user}/application_credentials/{cred}` looked a credential up by id without checking its owner, so a user
  could show or delete another user's credential through their own user reference; they now answer 404, as rotate
  already did. A login by `application_credential_name` alone now tries every enabled credential of that name (at most
  5) and accepts the one whose secret fits, instead of failing when another user's credential of the same name is found
  first.
- **An application credential carries only roles its user holds.** `roles` are now checked when the credential is
  created (400 for an invalid list, 403 for a role the user does not hold) and again at every login, where the token gets
  the credential's roles that the user still holds (401 when none is left). Credentials created by an earlier version are
  trimmed the same way at login; no migration is needed. Upgrade soon: a credential could be created with more roles than
  its user had.
- **A domain's consolidated invoice is limited to the system admin and that domain's own admin.** `GET
  /billing/invoices?domain_id=<id>` no longer returns the invoices of a domain the caller does not administer, and a
  domain admin gets 403 when creating an invoice for another domain.

### Changed
- **The browser console works through a proxy on the compute node.** `console-url` and `remote-consoles` return
  `http://<console_proxy_host>:6080/vnc.html?autoconnect=1&path=websockify%3Ftoken=<token>`. The new unit
  `cloudx-novnc-proxy` runs `websockify` and the noVNC client on port 6080 of every compute node; it ships in the
  `cloudx-compute-worker` package, which now depends on `novnc`, and the worker's unit has `Wants=` on it (on tarball
  nodes the guide's example worker unit has the line and its start command enables the proxy); the proxy unit has
  `PartOf=cloudx-compute-worker.service`, so it restarts and stops with the worker. The guest's VNC stays on
  127.0.0.1. The token is a JWT signed with a key derived from `auth_secret_key`, valid 300 seconds and good only for
  one VNC port of one libvirt domain on that node (`COMPUTE_HOST`); the proxy checks with libvirt that the domain still
  owns the port, and it is checked when the connection opens. The proxy logs every refusal with its reason. Before, the link pointed at `localhost:6080` and
  nothing in the packages started a proxy. To upgrade: let apt install `novnc` (tarball nodes: `apt install novnc`),
  open port 6080 to your users' browsers, and set `console_proxy_host` in
  `/etc/cloudx/compute-worker/compute-worker.conf` to an address the browsers can reach (default: the node name).
  `NOVNC_BASE_URL` is no longer read. See `docs/console.md`.

## 1.0.9 (2026-11-03)

One change since 1.0.9-rc1: `cloudx port delete` no longer deletes a port that a server uses when the port has no `device_id`
(below). The rest is in the rc1 section and the notes are in `docs/releases/v1.0.9.md`. The release files of 1.0.9-rc1 (the
checksums of the 48 `.deb` files matched `SHA256SUMS`) were installed on the lab (three controllers, two compute nodes,
controllers first, `cloudx-manage db sync` after the first) over the test packages and checked there: the unit list and
states and a hash of `/etc/cloudx` were identical to before on every node, no unit failed, no traceback in the logs, and
`vm-default` kept running (the same QEMU process). A resize waited in `VERIFY_RESIZE` and `confirm-resize` and
`revert-resize` ended it; stopping five servers at once left 0 of 10 running again after 80 seconds; a stopped server that
was resized and confirmed stayed `SHUTOFF`.

### Fixed
- `cloudx port delete` refuses a port that a server uses even when the port has no `device_id`. `server port add
  --network` makes a port with none, and the compute API does not set one when it attaches it, so the check of 1.0.9-rc1
  let through exactly the port a user adds to a running server: the lab deleted it, the logical port went from under the
  server, and `server port remove` then answered 404 because the dead id was still in the server record. The command now
  also reads the servers (`port_id` and `extra_port_ids`) and refuses a port that has a `device_owner` (a router interface,
  for example). `--force` still deletes it.

## 1.0.9-rc1 (2026-11-03)

Release candidate: it is installed on the lab before the final 1.0.9 is tagged. A `stop` that the system undid on its own,
a resize that waits for the user to confirm or revert it, `cloudx port delete`, and a quieter lb-worker, in the compute
service, the compute worker, the CLI, the orchestration engine and the lb-worker. **One schema change** (run
`cloudx-manage db sync`). The notes, with the upgrade steps and what was and was not tested, are in
`docs/releases/v1.0.9.md`.
The fixes were first built as test packages (named 1.0.9~rc0, rc0.1 and rc0.2, which are not this release) and installed on
the lab (three controllers, two compute nodes), where they were checked with CirrOS servers: five servers stopped at once
were running again after 80 seconds in 7 of 15 stops before the fix and in 0 of 15 after it; a resize waited in
`VERIFY_RESIZE` and `confirm-resize` and `revert-resize` ended it, also for a stopped server, which stayed stopped.

### Fixed
- **`cloudx server stop` no longer gets undone.** The worker reported `SHUTOFF` as soon as it had asked the guest to power
  off. If the power-state loop ran in the seconds before the guest was off, it saw a running domain under a `SHUTOFF`
  record and set `ACTIVE`, and one pass later auto-recovery found the domain off under an `ACTIVE` record and started
  it: the server the user had just stopped came back. On the lab, stopping five CirrOS servers at once left 7 of 15
  running again 80 seconds later; with the fix, 0 of 15. The worker now waits until the domain is off, up to
  `STOP_SHUTDOWN_WAIT` seconds (60, as in Nova), and destroys a guest that ignores the request. The status stays
  `STOPPING` meanwhile. The same function ends a relocated `SHUTOFF` server in `SHUTOFF`, which used to leave a running
  domain under that record when the guest ignored the first, early request.

### Added
- `cloudx port delete REF`. A port that a server still uses (the port has a `device_id`) is refused with a pointer to
  `cloudx server port remove`; `--force` deletes it anyway. Before, a port that `server port remove` had detached stayed
  in the project until someone called the API by hand.

### Changed
- **A resized server waits in `VERIFY_RESIZE`** until `confirmResize` or `revertResize`, as in Nova. It went straight to
  `ACTIVE` with its rollback snapshot still in the record, so a second resize silently replaced the size to go back to.
  Now: a resize is accepted only from `ACTIVE` or `SHUTOFF` (409 otherwise, and 409 from `VERIFY_RESIZE` until the resize
  is confirmed or reverted); `confirmResize` needs `VERIFY_RESIZE` (or an `ACTIVE` server that an older version left with
  its snapshot); `revertResize` works from `VERIFY_RESIZE`, from `ERROR` (a resize that failed) and from such an `ACTIVE`
  server, and the worker ends it with `ACTIVE`. `cloudx server confirm-resize` and `revert-resize` are new, and `cloudx
  server resize` tells the user about them. A stack update that changes a server's flavor confirms the resize itself. The
  dashboard already had both buttons for `VERIFY_RESIZE`, now enabled in that status only. A script that resizes and then
  uses the server must wait for `VERIFY_RESIZE` (the new size is already in the guest) and may confirm at once.
- **A resized server that was stopped ends stopped.** The worker and `confirmResize` set `ACTIVE` whatever the server was
  before, so the power-state loop found a stopped domain under an `ACTIVE` record and its auto-recovery started it as if
  it had crashed. The compute record now keeps the status from before the resize (`original_status`) and confirm and revert
  restore it.
- **Schema change:** `compute_servers.original_status` (migration `compute_0004`). Run `cloudx-manage db sync` after the
  upgrade, before the compute API serves resize.
- The lb-worker logs a failing poll of load-balancer-api once, then again every 5 minutes while it keeps failing (and
  says when it works again), instead of a warning every 5 seconds. A compute node of a cloud that does not run
  load-balancer-api logged the same line 720 times an hour. Every failure is still logged at debug level.

## 1.0.8 (2026-11-03)

No code change since 1.0.8-rc1; the full list is in the rc1 section below and the notes are in `docs/releases/v1.0.8.md`.
The release files of 1.0.8-rc1 (the checksums of the 48 `.deb` files matched `SHA256SUMS`) were installed on the lab (three
controllers, two compute nodes, controllers first) over the test packages, and checked there: the unit list and states and a
hash of `/etc/cloudx` were identical to before on every node, no unit failed, no traceback in the logs of the changed
services, and `vm-default` kept running (the same QEMU process). A CirrOS server with two NICs, a Ceph and an LVM data
volume went from `small` to `medium` and back (the domain kept 2 vCPUs and 2 GiB as its limits), was shelved and unshelved
on the same node and on the other one with both NICs and all three disks back, was snapshotted to an image that a new
server booted from, and was locked against a member user.

## 1.0.8-rc1 (2026-11-03)

Release candidate: it is installed on the lab before the final 1.0.8 is tagged. Fixes for six findings of a lifecycle sweep
on the 1.0.7-rc1 lab (a server with two NICs and a Ceph and an LVM data volume went through every server action), in the
compute, block-store and image services and their workers. No schema change and no change in the CLI. The notes, with the
upgrade steps and what was and was not tested, are in `docs/releases/v1.0.8.md`.
The fixes were first built as test packages (also named 1.0.8~rc1, up to rc4, which are not this release) and installed on the
lab (three controllers, two compute nodes), where they were checked with CirrOS servers:
- Resize from `small` to `medium` (1 to 2 vCPUs, 1 to 2 GB) ended `ACTIVE` in 9 s with the larger size in the guest;
  `revertResize` gave back the flavor name `small`.
- Shelve and unshelve kept both NICs and all three disks, on the same node and on the other one (where the LVM volume came
  in over iSCSI), with 4/4 pings on each network.
- Server snapshots of a Ceph and of an LVM root volume ended as `ACTIVE` private images (12 s and about 2 min), the volume
  snapshot was removed, and a server built from the Ceph image booted with a file that had been written in the original.
- Deleting servers whose root volumes had snapshots left the volumes `available` with their snapshots, and nothing on Ceph
  or LVM that the database did not know about.
- A member user got 409 on stop, reboot, resize, pause, rebuild, password, shelve, delete and volume attach of a locked
  server, could still read it, and an admin could still stop it.

### Fixed
- Resize to a flavor with more vCPUs or more memory than the server was created with no longer ends in `ERROR`. libvirt
  refuses to hot-plug past the maximums the domain was defined with. Inside them the change stays live; beyond them the
  worker redefines the domain with the new size and restarts it (ACPI shutdown first, destroy after 30 s without an
  answer; `RESIZE_SHUTDOWN_WAIT` sets the wait).
- `revertResize` puts the old flavor name back. Resize stored the flavor id where create stores the name, so the revert
  could not find the old flavor and kept the new one's name. The revert also reported `ACTIVE` before the worker had
  restored the size.
- `shelve` offloads the server. It called a block-store route that does not exist and only logged the 404, so the VM was
  stopped and called offloaded while its domain, taps and iSCSI sessions stayed. Now the worker removes them, the
  placement allocation is released, and the ports and volumes stay on the record. `unshelve` builds the domain again on a
  node placement picks, with the same ports and data volumes (the path evacuate uses). No image is made.
- `cloudx server snapshot` works for servers that boot from a volume, which is every server. It asked image-api to import
  the snapshot from a path, which image-api cannot read and the snapshot did not have yet (404 "source image path not
  found"), and left a volume snapshot behind. Now block-store snapshots the root volume (`POST /volumes/{ref}/upload-image`)
  and the block-store-worker that holds the volume exports it, converts it to qcow2, uploads it to image-api as a private
  image of the server's project and removes the volume snapshot. The call answers 202 and the image appears when the upload
  ends; a name that an image already has is refused with 409 first, and a failed upload leaves the volume snapshot in
  `error` with the reason. image-api lets a service name the owner of an uploaded image (`project_id` form field, internal
  token only). The Ceph export no longer fails with "File exists": `rbd export` will not write over the temporary file the
  caller had made. `cloudx volume backup` of a Ceph volume calls the same function with the same kind of file; it was not
  tried on the lab.
- Deleting a server whose root volume has snapshots no longer loses track of them. The internal delete that the server
  delete uses skipped the "volume has snapshots" check of `DELETE /volumes`, so the row went and the RBD image and its
  snapshot stayed on Ceph (on LVM the snapshot record went to `error`). Now the server is deleted and the root volume stays
  `available` until its snapshots are deleted.
- Lock covers every action that changes a server, not only delete, shelve and unshelve: start, stop, reboot, pause,
  resume, suspend, unsuspend, resize, confirm and revert resize, rebuild, rescue, unrescue, set password, adding and
  removing a port, and attaching and detaching a volume (also through `/volumes/{ref}/attach`, which reaches compute-api
  with the internal token). A member gets 409. Admins and services still bypass the lock, and snapshots and reads stay open.

- A volume made from an image whose size is exactly the volume size no longer fails on Ceph with `rbd: new size is equal to
  original size`. The image of a server snapshot is always that size, so a server could not be built from its own snapshot.

### Changed
- `POST /servers/{ref}/live-migrate` answers 501 and changes nothing. The worker read `server_id` where the API sends `id`,
  so the request left the server in `MIGRATING` until a `start`. Live migration is planned; cold migration
  (`POST /servers/{ref}/migrate`) and evacuate are the supported ways to move a server.

## 1.0.7 (2026-11-03)

No code change since 1.0.7-rc1; the full list is in the rc1 section below and the notes are in `docs/releases/v1.0.7.md`.
The candidate was installed on the lab (three controllers, two compute nodes) and checked there: a NIC and two data volumes
(`ceph-rbd` and `gp2`) attached after creation survived `reboot --hard`, and a server with two NICs kept both through
`rebuild`, `rescue` and `unrescue`, with 4/4 pings on each network.

### Documentation
- `docs/releases/v1.0.7.md` and `docs/releases/v1.0.6.md` carry the lab results found after 1.0.6 (dual-stack `slaac` port,
  explicit IPv6 `fixed_ips`, rebuild).
- The supported OVN is stated: the one from the Ubuntu 24.04 archive, 24.03.8 in `noble-updates`, which is what the lab tests of
  1.0.5 to 1.0.7 ran on. An older OVN is not tested (`docs/deb-install.md` section 8, `docs/releases/v1.0.7.md`).

## 1.0.7-rc1 (2026-11-02)

Release candidate: it is installed on the lab before the final 1.0.7 is tagged. Two bug fixes in the compute service and the
compute worker; no schema change and no change in the CLI. The notes, with the upgrade steps and what was and was not tested,
are in `docs/releases/v1.0.7.md`.
The candidate was installed on the lab (three controllers, two compute nodes) and checked there: a NIC and two data volumes
(`ceph-rbd` and `gp2`) attached after creation survived `reboot --hard`, and a server with two NICs kept both through
`rebuild`, `rescue` and `unrescue`, with 4/4 pings on each network.

### Fixed
- `cloudx server reboot --hard` no longer drops the NICs that were added after the server was created. It destroyed and
  undefined the domain and defined it again from the `domain.xml` written at creation, which does not have a NIC attached
  later (`POST /servers/{id}/ports`); the record still listed the port and the guest did not have it. A hard reboot now stops
  the domain and starts it again from the definition libvirt holds. The saved XML is used only when libvirt no longer has the
  domain. (#90)
- `rebuild`, `rescue` and `unrescue` keep every NIC of a server on several networks. They defined the domain with the primary
  NIC only: on the lab a server with two NICs had one after each of the three. The compute API now sends every port of the
  server to the worker, as evacuation already did, and the worker defines one NIC per port. The config drive of the three
  actions now also describes the primary port (it was sent empty). (#91)

## 1.0.6 (2026-11-02)

No code change since 1.0.6-rc1; the full list is in the rc1 section below and the notes are in `docs/releases/v1.0.6.md`.
The candidate was installed on the lab (three controllers, two compute nodes): a server made with two `--network` values had
two NICs, both ports bound, and kept both after an evacuation with 0% loss on each; a repeated network was refused.

## 1.0.6-rc1 (2026-11-02)

Release candidate: it is installed on the lab before the final 1.0.6 is tagged. One change, in the CLI only; the services, the
API and the database are the same as in 1.0.5. The notes, with the upgrade steps and what was and was not tested, are in
`docs/releases/v1.0.6.md`.
The candidate was installed on the lab (three controllers, two compute nodes) and checked there: a server made with two
`--network` values has two NICs, both ports bound, and keeps both after an evacuation with 0% loss on each; a repeated
network is refused.

### Added
- `cloudx server create` takes `--network` more than once: the first is the primary network, every other one adds a NIC. The
  API has taken several networks since 1.0.2; the CLI sent only one, and a second `--network` was silently ignored. A network
  given twice is refused before anything is sent.

### Documentation
- `docs/releases/v1.0.5.md` and `docs/releases/v1.0.4.md` carry the lab results found after those releases (Ubuntu guest,
  `dhcpv6-stateful`, a security group, evacuation of a one-NIC server). `docs/ipam.md`, `docs/feature-comparison.md` and
  `docs/releases/v1.0.3.md` no longer say that a security group which drops ICMPv6 drops the router advertisements: on the lab
  it does not.

## 1.0.5 (2026-11-02)

No code change since 1.0.5-rc1; the full list is in the rc1 section below and the notes are in `docs/releases/v1.0.5.md`.
The candidate was installed on the lab (three controllers, two compute nodes) and checked with a gateway-less IPv6 subnet
(refused with 400) and a CirrOS guest on a `slaac` subnet that got the RDNSS in its router advertisement and used it as its
only resolver.

## 1.0.5-rc1 (2026-11-02)

Release candidate: it is installed on the lab before the final 1.0.5 is tagged. Two changes, both from the known limitations
of 1.0.4. No schema change. The notes, with the upgrade steps and what was and was not tested, are in `docs/releases/v1.0.5.md`.
The candidate was installed on the lab (three controllers, two compute nodes) and both changes were checked there: the
gateway-less subnet is refused with 400, and a CirrOS guest on a `slaac` subnet got the RDNSS in its router advertisement and
used it as its only resolver.

### Fixed
- `cloudx router add-interface` on an IPv6 subnet that was created without a gateway answered 502
  (`ovn-nbctl: invalid network address: /64`), because the router port takes its address from the gateway. It now answers 400
  before anything reaches OVN, and says to create the subnet again with `--gateway` (a subnet cannot be edited).

### Added
- The router advertisements of a router port on an IPv6 subnet carry a DNS server (RDNSS, RFC 8106): the first IPv6 address
  in the subnet's `--dns`. OVN takes one RDNSS server per port. Router ports that exist get it when networking-api starts.
  IPv6 subnets have no default DNS, so a subnet advertises one only if it was made with an IPv6 `--dns`.

## 1.0.4 (2026-11-01)

No code change since 1.0.4-rc1; the full list is in the rc1 section below and the notes are in `docs/releases/v1.0.4.md`.
The candidate was installed on the lab (three controllers, two compute nodes) and checked with a server on two networks
that was evacuated while its source host was alive, and with Ubuntu 24.04 and CirrOS guests on `slaac` and
`dhcpv6-stateless` subnets.

## 1.0.4-rc1 (2026-11-01)

Release candidate: it is installed on the lab before the final 1.0.4 is tagged. Two changes, both found on the 1.0.3 lab.
No schema change. The notes, with the upgrade steps and what was and was not tested, are in `docs/releases/v1.0.4.md`.
The candidate was installed on the lab (three controllers, two compute nodes) and both changes were checked there: an evacuated
two-network server left no tap and no port claims on the source node, and Ubuntu and CirrOS guests on `slaac` and
`dhcpv6-stateless` subnets had exactly one global address, the allocated one.

### Fixed
- After an evacuation the compute node that still had the old domain destroyed it but left its taps in Open vSwitch.
  A leftover tap keeps its `iface-id`, so `ovn-controller` on that node and on the new host both claimed the port
  (about 60 claims a minute on each node, the port's chassis flipping every ~2 s). The node now deletes the taps of
  a stale domain with the domain. Taps left by an earlier evacuation stay until they are removed by hand.

### Changed
- On a subnet whose `ipv6_address_mode` is `slaac` or `dhcpv6-stateless`, a new port gets the EUI-64 address of its MAC,
  which is what the guest builds for itself, so the port, the server record, the OVN addresses and the guest agree on one
  address (Ubuntu had two, CirrOS only the one CloudX did not allocate). If that address is taken the port gets another
  MAC. An address named in `fixed_ips` is kept. `subnet create` refuses these modes on a prefix that is not a /64.
  Ports that exist keep their address.

## 1.0.3 (2026-11-01)

No code change since 1.0.3-rc2; the full list is in the rc2 and rc1 sections below and the notes are in
`docs/releases/v1.0.3.md`. The candidates were installed on the lab (three controllers, two compute nodes) and checked
with an Ubuntu 24.04 guest, a CirrOS guest and a router port made before the upgrade.

## 1.0.3-rc2 (2026-11-01)

Second release candidate, after rc1 was installed on the lab. The rc1 section below is part of it; the notes are in
`docs/releases/v1.0.3.md`.

### Added
- A router port on an IPv6 subnet sends router advertisements. DHCPv6 gives an address but no prefix length and no
  routes, so a DHCPv6 client got a /128 and could not reach its gateway or a neighbour. The mode is the subnet's
  `ipv6_ra_mode` (else `ipv6_address_mode`, else `dhcpv6-stateful`), the MTU the network's. Router ports that already
  exist get it when networking-api starts. Best effort: a refusal is logged and the interface is kept. No `rdnss` yet.

## 1.0.3-rc1 (2026-10-31)

Release candidate: it is installed on the lab before the final 1.0.3 is tagged. One fix, found with the Ubuntu 24.04
guest on the 1.0.1 lab. No schema change. The notes, with the upgrade steps and what was and was not tested, are in
`docs/releases/v1.0.3.md`.

### Fixed
- The default `user-data` keeps the image's own user. It listed `users: [{name: cirros, ...}]`, which replaces the image's
  user, so an Ubuntu server had no `ubuntu` user. It lists `users: [default]` now and the key goes to the default user
  (`ubuntu` on Ubuntu, `cirros` on CirrOS). On an image that is not CirrOS, use that user instead of `cirros@`.

## 1.0.2 (2026-10-31)

No code change since 1.0.2-rc2; the full list is in the rc2 and rc1 sections below and the notes are in
`docs/releases/v1.0.2.md`. The candidates were installed on the lab (three controllers, two compute nodes) and checked
with an Ubuntu 24.04 guest. Deleting a server now also removes the taps of ports added later with `server port add`,
which 1.0.1 left behind on the compute node.

## 1.0.2-rc2 (2026-10-31)

Second release candidate, after rc1 was installed on the lab. The rc1 section below is part of it; the notes are in
`docs/releases/v1.0.2.md`.

### Fixed
- A build no longer waits for its root volume inside the compute worker's RPC handler. The worker runs one handler at a
  time, so the loop (up to 300 s) stalled every other request for the node, and a 600 MB Ubuntu image took about 17
  minutes to import into Ceph on the lab, so the server went to `ERROR` although the volume became available later. The
  handler now looks at the volume once and, while it is being created, puts the build back on the node's queue. The
  wait is limited by the new setting `root_volume_wait_timeout` (3600 s by default, in `compute-worker.conf`): a volume
  that stays `creating` for good now holds the server in `BUILD` for up to an hour instead of 5 minutes.

## 1.0.2-rc1 (2026-10-31)

Release candidate: it is installed on the lab before the final 1.0.2 is tagged. Two fixes found by running the 1.0.1 cloud
with an Ubuntu 24.04 guest. No schema change. The notes, with the upgrade steps and what was and was not tested, are in
`docs/releases/v1.0.2.md`.

### Fixed
- A server created on several networks gets one NIC per port. Only the primary port was plugged: the extra ports were
  allocated and recorded as attached, but the guest had a single NIC (and `server port add --port` answered 409). The
  extra NICs are plugged on create and on evacuation, their taps are removed when the build fails or the server is
  deleted, and `network-config` configures them (`eth1`, ...; only `eth0` has a default route).

### Added
- `cloudx server create --ipv6` (`"ipv6": true` in the create request) gives a server an IPv6 address on each of its
  networks. On a dual-stack network the create request could only ask for IPv4. A network without an IPv6 subnet is
  refused with 400 before the root volume is created.

## 1.0.1 (2026-10-30)

No code change since 1.0.1-rc2; the full list is in the rc2 and rc1 sections below and the notes are in
`docs/releases/v1.0.1.md`. The candidates were installed on the lab (three controllers, two compute nodes) and checked.

## 1.0.1-rc2 (2026-10-30)

Second release candidate, after rc1 was installed on the lab. The rc1 section below is part of it; the notes are in
`docs/releases/v1.0.1.md`.

### Fixed
- The DHCPv6 options row of an IPv6 subnet is created. ovn-nbctl refused `cidr=fd42:7:7::/64` ('unexpected ":" parsing
  string'): the values of the row are OVSDB strings and are now quoted. The error was only logged, so rc1 still left
  `dhcp_options_id` empty on the lab. DHCPv4 needs no quotes and is unchanged.
- A Ceph snapshot no longer turns to `error` a minute after it is made. The block-store reconciler looked for an image
  called `snap-<id>`, which does not exist, instead of the snapshot `pool/image@snap` it recorded, so every Ceph snapshot
  was marked `error` while the worker log said it had just created it (a bug since 1.0.0, seen on the lab with rc1). A
  snapshot already marked `error` recovers by itself once the snapshot is found.

## 1.0.1-rc1 (2026-10-30)

Release candidate: it is installed on the lab before the final 1.0.1 is tagged. Fixes for what the 1.0.0 cloud showed
on the lab, and IPv6 for servers. No schema change. The notes, with the upgrade steps and what was and was not
tested, are in `docs/releases/v1.0.1.md`.

### Added
- A server on a network that has only an IPv6 subnet gets an address. `create_port` only looked at the IPv4 subnet, so
  the port had no address and the server showed none. A port on such a network now gets an IPv6 address (on a network
  that also has an IPv4 subnet IPv6 stays opt-in through `fixed_ips`), the server record shows the IPv6 address when
  there is no IPv4 one, and the config drive and `network_data.json` carry the IPv6 address, prefix and gateway. Config
  drives write `dhcp4: false` for an IPv6-only port instead of waiting for DHCPv4. `local-ipv4` never returns an IPv6
  address.
- `POST /servers/{ref}/unsuspend` and `cloudx server unsuspend`: restore a `SUSPENDED` server (409 in any other state).
  `start` also restores it.
- CLI: `subnet create` takes `--ipv6-ra-mode` and `--ipv6-address-mode` and works out the IP version from `--cidr`;
  `security-group rule add` takes `--ethertype` (IPv6 when `--remote-ip` is an IPv6 prefix).
- `scripts/check_feature_comparison.py`, run by the tests: it fails when `docs/feature-comparison.md` names an endpoint
  that is not a route.

### Changed
- The default security group has the same rules for IPv6 as for IPv4: egress to `::/0`, and ingress for ssh and ICMPv6
  under the same `DEFAULT_SECURITY_GROUP_ALLOW_SSH` / `DEFAULT_SECURITY_GROUP_ALLOW_ICMP` switches. networking-api adds
  them to an existing default group when it starts, so after an upgrade a server with an IPv6 address accepts ssh and
  ping over IPv6 from anywhere, as it already does over IPv4. Before, IPv6 traffic was dropped. An ACL whose remote
  prefix is `::/0` is written as plain `ip6`, since OVN drops an ACL it cannot compile without saying so.

### Fixed
- A suspended server stays `SUSPENDED`. libvirt calls a domain suspended to disk "shut off", and the compute worker's
  power-state sync (every 30 s) rewrote the status to `SHUTOFF`. The worker now tells a domain with a managed save apart
  and keeps the status. A server that is `ACTIVE` in the database while its domain is suspended (after `resume`, or a host
  that saved its guests on shutdown) is restored by the same auto-recovery pass that restarts a shut-off domain.
- A second dual-stack port on the same network no longer fails with a 502. IPv6 addresses held by other ports were not
  counted, so every automatic allocation returned the same address (`::2`) and OVN refused the duplicate. An IPv6 address
  given in `fixed_ips` was never checked either: it could be a duplicate or lie outside the subnet. It now gets the same
  checks as an IPv4 address (400 outside the subnet or not an address, 409 when taken or the gateway).
- The DHCPv6 options row of an IPv6 subnet used an IPv6 address as `server_id`; OVN wants an Ethernet address, so the
  row was refused (the error was only logged) and `dhcp_options_id` stayed empty. It now uses a MAC, adds
  `dhcpv6_stateless` for the stateless mode and the subnet's DNS servers, and ports on the subnet are bound to it with
  `lsp-set-dhcpv6-options`. Not run against OVN: the binding is best effort and cannot fail a port. An IPv6 subnet no
  longer needs a gateway.
- `PATCH /volume-types/{ref}` exists. `cloudx volume-type set` and the dashboard's API client already called it and got a 405. It changes
  `description`, `max_iops`, `max_throughput` and `is_public`; `backend`, `disk_type` and `lvm_type` are refused with 422.
- The CLI finds the auth API again after `cloudx auth login --auth-url`. The address was written to `~/.cloudx/config` and
  never read back, so on any machine other than the controller every later command went to `127.0.0.1:8005` unless
  `CLOUDX_AUTH_URL` was exported. The `[default]` section that `auth login` writes (`auth_url`, `project`, `output`) is
  now read; `CLOUDX_AUTH_URL` still wins.
- `cloudx subnet create --dns-nameserver` was answered with HTTP 422 (a joined string was sent for a list field).
- `cloudx server volume attach` and `detach` posted to the compute endpoint, which also needs the volume's path, and were
  answered with HTTP 422. They now call block-store, like `cloudx volume attach` and `detach`.
- Deleting a volume that still has snapshots is refused (409). It used to drop the database row and leave the backend
  volume behind for good (on Ceph, `rbd rm` refuses an image that has snapshots).
- Deleting a Ceph snapshot removes the RBD snapshot (`rbd snap rm pool/image@snap`). The worker ran `rbd rm` on a name
  that does not exist, logged a warning and dropped the record, so the snapshot stayed on the cluster.

### Documentation
- A tutorial for a highly available control plane with LVM and Ceph volumes (`docs/tutorials/ha-control-plane.md`); the
  docs are organised by deployment and storage backend, with notes on which nodes join the Ceph networks and where iSCSI
  should run in production.
- `docs/feature-comparison.md` was rewritten against the code (it described phases 1 to 5 and listed shipped features as
  missing); `docs/ipam.md` and `docs/security-group.md` cover IPv6.

## 1.0.0 (2026-10-28)

No code change since 1.0.0-rc1; the full list is in the rc1 section below and the notes are in `docs/releases/v1.0.0.md`.

### Documentation
- How to download and check the release assets: `docs/deb-install.md` section 3 ("Downloading a release"), the quickstart and the README.
- The tarball-to-packages upgrade runbook was fixed after a rehearsal with the rc1 assets: it installs from the downloaded
  files, keeps `cloudx-storage-init.service` (without it a loop-file volume group is gone after a reboot) and checks
  `system-id.conf` on compute nodes set up by 0.8.x.
- Troubleshooting entry for name lookups from a VM that take 5 seconds because a resolver in `dns_upstream` does not answer.
- Not tested: a cloud with the controller and the compute nodes on separate machines running different versions during the upgrade.

## 1.0.0-rc1 (2026-10-27)

The notes for the release this candidate leads to are in `docs/releases/v1.0.0.md`.

### Added
- OpenStack-style .deb packages: `cloudx-common`, `cloudx-db`, one package per API or daemon, role meta packages
  (`cloudx-controller`, `cloudx-compute`, `cloudx-storage`) and a standalone `cloudx-cli`. The twenty stateless APIs sit
  behind nginx. Built from `packaging/manifest.toml` with `make deb`; the venv is pinned with pip constraints.
- A service catalog in auth-api (`cloudx-manage catalog sync`). Services, workers, host daemons and the CLI resolve
  their peers through it instead of per-service URLs in config.
- `cloudx-manage` (`db sync`, `conf init`) and host setup scripts shipped with the role packages:
  `cloudx-setup-compute`, `cloudx-setup-ceph-secret`, `cloudx-setup-storage`.
- Messaging (Zaqar-like queues) and Reservation (Blazar-like leases) services.
- An all-in-one quickstart for the .deb packages (`docs/quickstart.md`), the full deb install guide and a lab upgrade
  runbook (`docs/deb-install.md`), and documented known limitations.
- Release assets: the `.deb` files, the tarball and `SHA256SUMS` are attached to each GitHub release. `docs/deb-install.md`
  section 3 ("Downloading a release"), the quickstart and the README show how to download and check them.

### Removed
- Files left over from the Docker Compose era and one-off scripts: the container entrypoints and reference configs under
  `services/` (`ovn`, `postgres`, `rabbitmq`, `haproxy`, `keepalived`, `backup`, `certs`, `grafana`, `prometheus`),
  `make generate-certs`, `.env.example`, the old per-service CLIs in `cli/` (`cloudx` replaces them), `scripts/bootstrap.sh`,
  `postgres-init/`, the phase 7 and 8 test scripts, the VirtualBox and PowerShell lab helpers, `docs/superpowers/` and an empty `CLAUDE.md`.
  The docs that described them say what is not provided.
- The "phase" prefix of three docs: `production-readiness.md`, `telemetry-billing-events.md`, `orchestration-vpc-bgp.md`.

### Fixed
- RBD volumes boot and attach against a real cephx cluster (Ceph 19.2.3 clients).
- Router gateways land on a real gateway chassis, and the node's OVN system-id survives an Open vSwitch restart.
- A clean .deb install now boots a VM: stale chassis cleanup, `ovn-bridge-mappings`, block storage preparation.
- Deleting a security group removes its OVN port group and ACLs; rule ports set from the CLI are kept.
- Subnets created without a nameserver no longer get the lab's resolver address (`172.20.0.118`) on an install without the
  DNS service: VMs get `dns_upstream` instead. `cloudx.conf.sample` ships `dns_public_address` and `dns_resolver_address`
  commented out.
- Node configuration files are written mode 640, owner `root:cloudx`, with the controller's credentials.
- The RabbitMQ password is no longer written to logs; RPC connections use TCP keepalive, so a dead peer is noticed in
  about a minute.
- A server deleted while it is still in BUILD no longer leaves a running domain behind; tap devices are recreated
  before a domain starts after a host reboot.
- ha-watchdog falls back to the last ha-api URL that resolved when the catalog is down.
- `cloudx ha protect` and `unprotect` accept a server name, as the CLI sends it (they only took an ID before).
- A missing load balancer API no longer stops the DNS worker's sweep of automatic names.
- `cloudx flavor list` shows memory and disk.
- A server group no longer accepts a member on a node that already holds one when every node is taken (409
  `No valid host`, not a silent placement on a used node).
- `cloudx scaling-group scale-out/scale-in` and stack calls no longer answer 401 (orchestration accepts `X-Auth-Token`).
- The telemetry worker sends metrics again: it read the wrong key of the server list and looked up libvirt domains by
  the wrong name, so no CPU, memory, disk or network data reached telemetry-api.

### Changed
- Pre-release packages get a tilde in their Debian version (`1.0.0~rc1-1`), so `apt` sorts a release candidate before
  the release.
- All documentation is English; the tarball install guide moved to `docs/tarball-install.md`; new architecture page,
  CLI guide checked by a test, and tutorials (three-node cloud, auto-scaling, Ceph, Kubernetes, dashboard tour).

## 0.8.6 - 2026-10-20
Database backup and scheduled backups, Instance HA hardening (watchdog and a second ha-api with a leader lock), an error
reaper, iSCSI cleanup and an IP allocation race fix.

## 0.8.5 - 2026-10-20
Four new services, each "not 1:1" with its OpenStack counterpart: File Store (Manila-like), Container (Zun-like),
Database (Trove-like) and Instance HA (Masakari-like).

## 0.8.0 - 2026-10-18
DNS service (Designate-like, on BIND9), client CRL for load balancer listeners.

## 0.7.21 - 2026-10-17
L7 ordering fix for the application load balancer; per-domain unique names and domain quotas in auth.

## 0.7.20 - 2026-10-17
Domain isolation in auth, L7 parity for the application load balancer, dashboard pages for stacks, alarms and role
assignments.

## 0.7.19 - 2026-10-17
Volume replication, VPC peering, VPN as a service, BGP and the K3s Kubernetes engine move from stubs to working features.

## 0.7.18 - 2026-10-11
Service-to-service calls use a shared `internal_token` instead of the admin password.

## 0.7.17 - 2026-10-10
Closes most limitations carried over from 0.7.5 to 0.7.16 (networking, compute and telemetry migrations).

## 0.7.16 - 2026-10-10
Orchestration, load balancer, telemetry with alarms, and billing.

## 0.7.13 - 2026-10-10
Core parity: configuration moves to OpenStack-style `/etc/cloudx/cloudx.conf` plus per-service files; Placement becomes
the scheduler; server groups with affinity and anti-affinity.

## 0.7.9 - 2026-10-07
Hardening: `internal_token` required on every node for `/internal/*` routes.

## 0.7.6 - 2026-10-07
`/metrics` on every API requires a bearer token. Fixes for items left open after 0.7.5.

## 0.7.5 - 2026-10-07
Telemetry, billing and events work end to end, with the dashboard pages on top of them.

## 0.7.0 - 2026-10-05
No new API features. Secrets are mandatory (no default passwords or keys), the database schema is managed by Alembic, and
volume quotas are enforced.

## 0.6.0 - 2026-10-03
Native systemd deployment replaces Docker Compose. Dashboard and CLI cover the full API.

## 0.2.5 - 2026-08-21
Production readiness and multi-node architecture.

## 0.2.0 - 2026-08-07
Third roadmap phase complete (preceded by 0.2.0-alpha-1 and 0.2.0-alpha-2 in June).

## 0.1.0 - 2026-06-13
First tagged release: single-node cloud on libvirt/QEMU/KVM and OVN, with volume types (LVM and Ceph) and a dashboard.
