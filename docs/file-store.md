# File Store (Manila-like, NFS)

NFS shares that VMs mount over the tenant network. It covers Manila's core features, not 1:1 (see the limits).

## Components

| Unit | Node | Purpose |
|---|---|---|
| `cloudx-file-store-api` | controller | API (port 8017): share networks, shares, access rules |
| `cloudx-file-store-worker` | storage (`file_store_host`) | one LV + ext4 per share, one NFS-Ganesha per share network in the netns `qfs-<id>` |

A share network is an OVN port on the tenant subnet. Its address becomes the mount target. VM traffic is not NATed, so per-VM ACLs apply. Ganesha is NFSv4 only, without rpcbind, on port 2049.

## Configuration (`/etc/cloudx/cloudx.conf`)

`file_store_api_url`, `file_store_host` (the same as the worker node's `node_name`), `file_store_mount_root`. On the worker node, `/etc/cloudx/file-store-worker/file-store-worker.conf` holds `file_store_api_url` pointing at the controller and `file_store_host`. The installer installs `nfs-ganesha` only on that node.

## Usage

```
cloudx file-store network create sn --network <net-id>
cloudx file-store share create data --size 5 --network <sn-id>
cloudx file-store access add <share-id> 10.0.0.5 --level rw
cloudx file-store share extend <share-id> 10
# on the VM:
mount -t nfs4 -o vers=4.1 <ip>:/<share-id> /mnt
```

Without an access rule, nobody can mount. Addresses/CIDRs wider than /8 are rejected (`0.0.0.0/0` is not possible). Root is always squashed.

## Proven in the lab

Ganesha listens on the port IP inside the netns without rpcbind; a VM on a compute node mounts and writes with its real IP; a VM without a rule is rejected; deleting the rule empties Clients; extending from 1 to 2 GB while mounted works; deleting the share and the share network cleans up the LV, mount, netns, OVS port and OVN port.

## Limits

No snapshots, clones, shrink, quotas, SMB/CIFS, replication, NFSv3 or Kerberos. One worker node. The share root is mode 0777 (access is controlled by the IP ACL and Root_Squash). Restarting the worker does not cut NFS (`KillMode=process`), but restarting Ganesha triggers a 10-second grace period. Details: `docs/releases/v0.8.5.md`.
