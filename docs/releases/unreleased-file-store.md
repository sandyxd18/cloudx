# CloudX (version to be assigned) - File Store

New File Store service (Manila-like, NFS). There are **upgrade steps**.

## Upgrading from v0.8.0
1. Set `file_store_api_url`, `file_store_host` (the worker node's `node_name`) and `file_store_mount_root` in `/etc/cloudx/cloudx.conf`; re-run `scripts/install-systemd.sh` (installs `nfs-ganesha` and `nfs-ganesha-vfs` on the worker node only).
2. On the worker node create `/etc/cloudx/file-store-worker/file-store-worker.conf` with `file_store_api_url` (controller) and `file_store_host`.
3. Run `cloudx-db-migrate file_store` from `/tmp`.
4. Enable `cloudx-file-store-api` on the controller and `cloudx-file-store-worker` on the storage node. Rebuild the dashboard.

## Changes
- **File Store** (`file-store-api` :8017, `file-store-worker`, CLI `cloudx file-store`, dashboard File Store page): share networks (an OVN port on a tenant subnet), shares (LV + ext4, online extend), access rules (`rw`/`ro`, per IP or CIDR).
- NFSv4 only via NFS-Ganesha, one process per share network in its own netns, no rpcbind. VM traffic is not NATed, so per-VM security rules apply.
- No rule means deny all. `0.0.0.0/0`, IPv6 and ranges wider than /8 are rejected. Root is always squashed.

## Known limitations
- Not Manila 1:1: no snapshots, clones, shrink, quotas, SMB/CIFS, replication, NFSv3 or Kerberos. API paths differ from Manila's.
- One worker node (`file_store_host`); no multi-node or HA.
- Share root is mode 0777; access relies on IP rules and Root_Squash, not per-share UID mapping.
- Ganesha has no parse-only check; configs are rendered only from validated inputs.
- Restarting Ganesha starts a 10 s NFSv4 grace period. Restarting the worker does not stop Ganesha.
- Verified in the lab with Ubuntu 24.04 guests; CirrOS has no NFSv4 client and does not run user-data.
