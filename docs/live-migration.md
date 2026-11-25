# Live migration and draining a compute node

Live migration moves a running server to another compute node without stopping it. `cloudx host drain` uses it to empty a node before maintenance. Cold migration (`POST /servers/{ref}/migrate`, which restarts the server on the target) still exists and is what `drain` uses for stopped servers.

> Status: the code is covered by unit tests with a fake libvirt and a fake RPC. It has not been run on a lab yet, so the network pause below is the design estimate and not a measurement.

## What can be moved

| Disk of the server | Live migration |
|---|---|
| Root or data volume on Ceph RBD | Yes. Every compute node opens the image. |
| LVM volume on another node (the server reaches it over iSCSI) | Yes. The target logs in to the same volume and the source logs out afterwards. |
| LVM volume on the node the server runs on | Only with `cloudx server live-migrate`: the target then reads the volume over iSCSI from the source node. `cloudx host drain` does **not** move such a server, because the volume stays on the node that is being emptied. It is skipped and listed. |

There is no block migration: CloudX never copies a volume over the network. A volume that lives on the node you want to empty has to be moved (or the server stopped and rebuilt elsewhere) before that node can be taken out of service.

Only `ACTIVE` servers can be moved live. Paused, suspended, stopped and other states are refused (`drain` cold-migrates `SHUTOFF` servers and skips the rest).

## Prerequisites

1. **ssh between the compute nodes.** The compute worker runs as root on every node and opens a libvirt connection `qemu+ssh://root@NODE/system` to the target. Nothing creates the keys for you: run the script from any machine that can ssh to all the compute nodes as a user with sudo (the lab notes use `ubuntu`):

   ```bash
   scripts/setup-migration-ssh.sh --dry-run comp01 comp02    # print the plan, run no ssh
   scripts/setup-migration-ssh.sh comp01 comp02 comp03
   ```

   For each node the script creates `/root/.ssh/cloudx_migration` (ed25519, no passphrase) when it is missing, adds the public key of every node to `/root/.ssh/authorized_keys` of every other node (no duplicates), fills `/root/.ssh/known_hosts` with the host keys of the other nodes (`ssh-keyscan`), and writes a `Host` block per other node in `/root/.ssh/config` that uses `IdentityFile /root/.ssh/cloudx_migration`. It can be run again. Use the same node names that `live_migration_host` will hold (by default the compute node name, `compute_host`), and make sure every node can resolve them. Use `--user NAME` or the environment variable `SSH_USER` for a different ssh user.

2. **Network between the compute nodes.** TCP 22 (the libvirt connection) and TCP 49152-49215 (the memory of the guest, sent by QEMU) must be open from every compute node to every other one. The memory travels unencrypted. Nothing else changes: iSCSI and Ceph work as before.

3. **Security consequence.** After step 1 every compute node can log in as root on every other compute node, with a key that has no passphrase. Anyone who gets root on one compute node gets root on all of them. Keep the compute nodes on a management network that only operators reach, and do not run the script if that trade is not acceptable. Removing the access later is a matter of deleting the `cloudx-migration@...` lines from `/root/.ssh/authorized_keys` and the `cloudx-migration` blocks from `/root/.ssh/config`.

4. **A version that knows the call.** Upgrade all compute nodes first, then compute-api. A worker that does not know the new calls answers them with an error, so a request sent to a node that has not been upgraded yet fails with that error and changes nothing.

## Configuration

All settings are optional and go in `/etc/cloudx/compute-worker/compute-worker.conf`, section `[DEFAULT]` (see `etc/cloudx/cloudx.conf.sample`). The same names in upper case work as environment variables.

| Key | Default | Meaning |
|---|---|---|
| `live_migration_host` | the node name (`compute_host`) | The name or address the other nodes use to reach this node. It is what is put into the URI below, so it must match the `Host` names the script wrote. |
| `live_migration_uri` | `qemu+ssh://root@{host}/system` | Template of the libvirt connection to the target. `{host}` is replaced by the `live_migration_host` of the target. |
| `live_migration_timeout` | `1800` | Seconds in total before the migration is aborted and rolled back. |
| `live_migration_max_downtime_ms` | `300` | The longest pause, in milliseconds, libvirt may take for the last step. |
| `live_migration_bandwidth_mbps` | `0` | Cap on the memory transfer in megabits per second. `0` means no cap. |
| `live_migration_auto_converge` | `false` | Slow the vCPUs of a guest that dirties memory faster than it can be sent, so the migration can finish. |

## Moving one server

```bash
cloudx server live-migrate web-1                  # the scheduler picks the target
cloudx server live-migrate web-1 --host comp02    # a given target
cloudx server show web-1                          # MIGRATING while it runs, ACTIVE when done
```

The call answers at once (`202`) with the source and target. The server stays `MIGRATING` and keeps its old `host` until the guest runs on the target, then `host` changes and the status goes back to `ACTIVE`. The policy action is `compute:server:live_migrate`, `role:admin` by default ([policy.md](policy.md)).

The request is refused with `409` (and nothing changes) when:

- the server is not `ACTIVE`, or it is locked (a lock holds for admins too: unlock it first);
- the server has no host, no root volume or no libvirt domain;
- a volume of the server is not `in-use` or has no path;
- the target is the server's own host, is disabled, is forced down, or is not a known compute node;
- no host can take the server (`No valid host was found`).

The placement claim of the server moves to the target when the migration starts and comes back to the source if it fails.

### The network pause

The guest is paused for a short moment while the last memory is copied (`live_migration_max_downtime_ms`). After that the network port of the guest has to be bound on the new node: the worker removes the tap on the source, then binds the port on the target. Expect a network pause of **one to three seconds**; Established TCP connections should survive a pause of that length. There is no guarantee of zero packet loss (OVN multi-chassis binding, which would avoid the pause, is not used).

## Draining a node

```bash
cloudx host drain comp01 --dry-run     # print the plan, change nothing
cloudx host drain comp01               # disable the node and move its servers away
cloudx host enable comp01              # after the maintenance
```

`drain` works as follows:

1. It disables the node (`disabled_reason` is `drain`), so the scheduler places nothing new on it. With `--dry-run` nothing is disabled.
2. It lists the servers on the node (at most 1000; the list has no pagination) and moves them **one at a time**:
   - `ACTIVE` server: live migration, then it waits until the server is `ACTIVE` on another host. The server is skipped, with the reason, when one of its volumes is a local LV on the node being drained (that disk cannot be live-migrated away);
   - `SHUTOFF` server: cold migration;
   - any other status: skipped, with the reason.
3. It prints a table of what happened to each server and exits non-zero when any server was skipped or failed.

`--timeout SECONDS` (default 2400) is how long it waits for **each** migration before counting it as failed; it is not a limit for the whole run. The node is disabled first and **stays disabled**, also after failures. Run `cloudx host enable NODE` to put it back in service. Running `drain` again only handles the servers that are still on the node.

`--dry-run` prints the same plan and changes nothing. Its exit code predicts the real run: it is non-zero when at least one server would be skipped.

## Limits and failure handling

**A migration that fails before it completes.** When the preparation on the target, the disk mapping or the libvirt migration fails (or `live_migration_timeout` passes), the worker rolls the target back: it removes the taps it made, logs out of iSCSI sessions it opened, removes the instance directory and stops a domain that libvirt may have created there. The server goes back to `ACTIVE` on the source host without an error state, and the placement claim goes back to the source. The reason is recorded in the instance actions of the server:

```bash
cloudx server show web-1                      # status ACTIVE, host unchanged
```

The reason is the message of the `live_migrate` entry of `GET /servers/{ref}/os-instance-actions` (there is no CLI command for it). Failures are also logged by `cloudx-compute-worker` on the source node.

**A migration that completed but was not cleaned up.** When the guest already runs on the target, but removing the old taps, the final port binding on the target, the iSCSI logout or the cleanup of the source directory failed, the action is reported as **success** with a message that starts with `migrated, but cleanup failed:` followed by the failed steps. The server is `ACTIVE` on the target. If the failed step was the final binding of the ports, the network of the guest may be down until an operator binds the ports on the target: on the target node check with `ovs-vsctl` that each tap of the guest (`virsh -c qemu:///system domiflist DOMAIN`) has `external_ids:iface-id` set (`ovs-vsctl get Interface TAP external_ids`), and read the worker log for the reason. If the status update itself could not be sent, the message says `the status update failed`.

**The worker of the source node restarts during a migration.** This is a known limitation. libvirt may still finish the move while the worker is gone. The server can then stay `MIGRATING` with the source as its host, and the ports on the target are not bound. When the worker starts it only puts a server back to `ACTIVE` when its domain is still running on that node. To recover, check both nodes:

```bash
sudo virsh -c qemu:///system list        # on the source and on the target
```

- The domain runs on the **target**: bind its ports on the target (see above) and fix the server record (`host` and status) in the compute database.
- The domain runs on the **source**: restart the worker (`sudo systemctl restart cloudx-compute-worker`) or run `cloudx server start web-1`, and the server returns to `ACTIVE`.

**Other limits.**

- No block migration, no TLS for the libvirt connection (the ssh connection is encrypted, the memory stream on 49152-49215 is not), no multi-chassis binding, no dashboard button, and no automatic evacuation by `ha-api`.
- `host drain` migrates one server at a time; a node with many large servers takes long.
- Servers with a local LV on the drained node are always left behind.

## Troubleshooting

```bash
sudo journalctl -u cloudx-compute-worker -n 200 --no-pager     # on the source and on the target
# can the source reach the target the way the worker does? (run on the source node)
sudo ssh -i /root/.ssh/cloudx_migration root@comp02 virsh -c qemu:///system list
sudo virsh -c qemu+ssh://root@comp02/system list               # the URI the worker uses
```

| Symptom | Likely cause |
|---|---|
| `live migration failed: ... Host key verification failed` or `Permission denied (publickey)` | The ssh trust is missing or the node name differs from the one in `known_hosts` and `config`. Run `scripts/setup-migration-ssh.sh` again with the names used in `live_migration_host`. |
| `live migration failed: ... Connection refused` or a timeout on port 49152-49215 | The memory ports are blocked between the nodes. |
| The migration fails at once with an unknown method error | The target node still runs an older worker. Upgrade the compute nodes before compute-api; with an older worker on the source node the server can stay `MIGRATING` (restart that worker, see above). |
| `server ... cannot be live-migrated (needs ACTIVE)` | The server is not `ACTIVE`, or another migration is running. |
| `drain` exits non-zero but every row says `skipped` | These servers have a local LV on the node, or are `PAUSED`, `SUSPENDED` and so on. Fix them one by one or accept that they stay. |
