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

1. **ssh between the compute nodes.** The compute worker runs as root on every node and opens a libvirt connection `qemu+ssh://root@NODE/system` to the target. Nothing creates the keys for you: run the script from any machine that can ssh to all the compute nodes as a user with passwordless sudo (the lab notes use `ubuntu`). The `cloudx-cli` package installs it as `cloudx-setup-migration-ssh`; in a source checkout it is `scripts/setup-migration-ssh.sh` (same script, same options):

   ```bash
   cloudx-setup-migration-ssh --dry-run comp01 comp02    # print the plan, run no ssh
   cloudx-setup-migration-ssh comp01 comp02 comp03
   scripts/setup-migration-ssh.sh comp01 comp02 comp03   # the same, from a source checkout
   ```

   For each node the script creates `/root/.ssh/cloudx_migration` (ed25519, no passphrase) when it is missing, adds the public key of every node to `/root/.ssh/authorized_keys` of every other node (no duplicates), fills `/root/.ssh/known_hosts` with the host keys of the other nodes (`ssh-keyscan`), and writes a `Host` block per other node in `/root/.ssh/config` that uses `IdentityFile /root/.ssh/cloudx_migration`. It can be run again. Use the same node names that `live_migration_host` will hold (by default the compute node name, `compute_host`), and make sure every node can resolve them. Use `--user NAME` or the environment variable `SSH_USER` for a different ssh user.

2. **Network between the compute nodes.** TCP 22 (the libvirt connection) and TCP 49152-49215 (the memory of the guest, sent by QEMU) must be open from every compute node to every other one. Both go to the target's `live_migration_host`: the worker passes it to libvirt as the address of the memory stream (`tcp://HOST`), so set it to an address on the network you want that traffic on. The memory travels unencrypted. Nothing else changes: iSCSI and Ceph work as before.

3. **A unique iSCSI initiator name per node.** The storage node keeps one access entry per initiator name, and during a live migration of a server with an LVM volume both compute nodes are logged in to that volume at once. Nodes cloned from one template share `/etc/iscsi/initiatorname.iscsi`; the target then refuses the migration (`... have the same iSCSI initiator name ...`) and nothing changes. Check with `sudo cat /etc/iscsi/initiatorname.iscsi` on each node, and give a node a new name when two match (log out of its iSCSI sessions first, or do it while no server with an LVM volume runs there):

   ```bash
   echo "InitiatorName=$(sudo iscsi-iname)" | sudo tee /etc/iscsi/initiatorname.iscsi
   sudo systemctl restart iscsid
   ```

   Servers on Ceph RBD only are not affected.

4. **Security consequence.** After step 1 every compute node can log in as root on every other compute node, with a key that has no passphrase. Anyone who gets root on one compute node gets root on all of them. The host keys that the script puts into `known_hosts` come from `ssh-keyscan`, which is trust on first use: it takes whatever answers on that name at that moment, so run the script only on a network you trust. Keep the compute nodes on a management network that only operators reach, and do not run the script if that trade is not acceptable. Removing the access later is a matter of deleting the `cloudx-migration@...` lines from `/root/.ssh/authorized_keys` and the `cloudx-migration` blocks from `/root/.ssh/config`.

5. **A version that knows the call.** Upgrade all compute nodes first, then compute-api. Follow that order: a node that has not been upgraded does not know the new calls (see the last rows of the troubleshooting table for what that looks like).

## Configuration

All settings are optional and go in `/etc/cloudx/compute-worker/compute-worker.conf`, section `[DEFAULT]` (see `etc/cloudx/cloudx.conf.sample`). The same names in upper case work as environment variables.

| Key | Default | Meaning |
|---|---|---|
| `live_migration_host` | the node name (`compute_host`) | The name or address the other nodes use to reach this node. It is what is put into the URI below, so it must match the `Host` names the script wrote, and it is also where the memory stream goes (ports 49152-49215). |
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

While a server is `MIGRATING`, start, stop, reboot, pause, resume, suspend, rebuild, rescue, unrescue, set-password, delete and detaching a port are refused with `409` (`server is MIGRATING (a migration is in progress); retry when it has finished`). Wait for the migration to end. The same holds during a cold migration.

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
2. It lists the servers on the node (it reads every page of the server list, 1000 servers per request) and moves them **one at a time**:
   - `ACTIVE` server: live migration, then it waits until the server is `ACTIVE` on another host. The server is skipped, with the reason, when one of its volumes is a local LV on the node being drained (that disk cannot be live-migrated away);
   - `SHUTOFF` server: cold migration;
   - any other status: skipped, with the reason.
3. It prints a table of what happened to each server and exits non-zero when any server was skipped or failed.

`--timeout SECONDS` (default 2400) is how long it waits for **each** migration before counting it as failed; it is not a limit for the whole run. The node is disabled first and **stays disabled**, also after failures. Run `cloudx host enable NODE` to put it back in service. Running `drain` again only handles the servers that are still on the node.

`--dry-run` prints the same plan and changes nothing. Its exit code predicts the real run: it is non-zero when at least one server would be skipped.

## Limits and failure handling

**A migration that fails before it completes.** When the preparation on the target, the disk mapping or the libvirt migration fails (or `live_migration_timeout` passes and the job is aborted), the worker first checks the source: it rolls the target back only when the domain still runs on the source and no migration call is left running. The rollback removes the taps it made on the target, logs out of iSCSI sessions it opened, removes the instance directory and stops a domain that libvirt may have created there. The server goes back to `ACTIVE` on the source host without an error state, and the placement claim goes back to the source. The reason is recorded in the instance actions of the server:

```bash
cloudx server show web-1                      # status ACTIVE, host unchanged
```

The reason is the message of the `live_migrate` entry of `GET /servers/{ref}/os-instance-actions` (there is no CLI command for it). Failures are also logged by `cloudx-compute-worker` on the source node.

**libvirt reported an error, but the guest had already moved.** libvirt can fail the call after the target has taken over the guest (the connection drops in the last step). When the domain is then gone from the source, the worker does not roll back: it finishes the migration as a success and the message starts with `migrated, but cleanup failed: libvirt reported an error after the guest had moved`.

**It is unclear where the guest runs.** When the migration call is still running 30 seconds after `abortJob`, the source domain cannot be looked up, or it is there but not running (paused, for example), rolling back could destroy the only running copy. The worker then changes nothing: no rollback, no report to compute-api (so the placement claim stays on the target), and the server stays `MIGRATING` with an `error_message` that starts with `live migration to NODE failed (...) and it is unclear where the guest runs`, logged at ERROR on the source. Lifecycle actions are refused while it is `MIGRATING`. Work out where the guest runs and settle it with the steps below.

**A migration that completed but was not cleaned up.** When the guest already runs on the target, but removing the old taps, the final port binding on the target, the iSCSI logout or the cleanup of the source directory failed, the action is reported as **success** with a message that starts with `migrated, but cleanup failed:` followed by the failed steps. The server is `ACTIVE` on the target. If the failed step was the final binding of the ports, the network of the guest may be down until an operator binds the ports on the target: on the target node check with `ovs-vsctl` that each tap of the guest (`virsh -c qemu:///system domiflist DOMAIN`) has `external_ids:iface-id` set (`ovs-vsctl get Interface TAP external_ids`), and read the worker log for the reason. If the worker's status update itself could not be sent, the message says `the status update failed`; compute-api then sets the host and `ACTIVE` itself from the report, as long as the server is still `MIGRATING`.

**The worker of the source node restarts during a migration.** libvirtd keeps running the migration job while the worker is gone. When the worker starts, for every `MIGRATING` server whose `host` is that node and whose domain is `RUNNING` there (`recover_migrating_servers`, `services/compute_worker/app/main.py`):

- a migration job that is still running is aborted, and the worker waits up to 30 seconds for it to end;
- if the domain then runs there with no job, the server goes back to `ACTIVE` and the action is closed as an error (`the compute worker restarted during the live migration; the migration was aborted`), which puts the placement claim back on the source;
- if the job does not stop, or the domain has left the node meanwhile, the server stays `MIGRATING` and the worker logs a warning that names it.

A server whose domain is not running on its `host` is left alone. The target may keep the unbound taps and iSCSI sessions that the preparation made; they do no harm, and the next migration to that node replaces them (`ip link` / `iscsiadm -m session` show them).

### Settling a server that stays `MIGRATING`

**Before any step below, run on both nodes:**

```bash
sudo virsh -c qemu:///system domjobinfo DOMAIN     # "Job type: None" means no migration job is running
sudo virsh -c qemu:///system list                  # which node runs the guest
```

`DOMAIN` is `libvirt_domain_name` in `cloudx server show web-1`. While `domjobinfo` shows a job on either node, wait or abort it there (`sudo virsh -c qemu:///system domjobabort DOMAIN`) and look again. Never remove taps or sessions while a job runs.

The steps that settle the record use compute-api's internal finish call, the one the worker makes. Run it on the controller with the internal token (`internal_token` in the `[DEFAULT]` section of the CloudX configuration; do not paste it into notes or tickets). It changes only a server that is still `MIGRATING`:

```bash
curl -sS -X POST http://127.0.0.1:8003/internal/servers/SERVER_ID/actions/finish \
  -H "X-Internal-Token: $TOKEN" -H 'Content-Type: application/json' \
  -d '{"result": "error", "action": "live_migrate", "message": "settled by an operator: the guest runs on the source"}'
```

*The domain runs on the source (and nowhere else).* The migration did not happen. Send the call above with `"result": "error"`: the server becomes `ACTIVE` on its host and the placement claim goes back to the source. Restarting the worker on the source (`sudo systemctl restart cloudx-compute-worker`) does the same.

If compute-api logged that the claim could not be put back (`... its placement claim could not be put back on ...`), restore it by hand: the placement API has `POST /allocations` (admin), which replaces all allocations of a consumer. The body is the server id as `consumer_uuid`, its `project_id`, and `{"<source resource provider uuid>": {"resources": {"VCPU": N, "MEMORY_MB": M}}}`, where N and M are the vCPUs and the RAM in MB of the server's flavor (`cloudx server show web-1` names the flavor, `cloudx flavor show FLAVOR` gives `vcpus` and `ram_mb`). `cloudx placement provider-list` shows the provider uuids. Without it the capacity accounting for that server is wrong (the target is charged and the source is not) until the server is moved, resized or deleted.

*The domain runs on the target.* The guest runs there, but its network may not be bound and the server record still says source. The placement claim is already on the target, which is right.

1. On the **source** node, and only there, remove the old taps, which still carry the `iface-id` of the port (two nodes with the same `iface-id` make `ovn-controller` fight over the port). **Run this only after `virsh domjobinfo DOMAIN` shows no job on both nodes and `virsh list` on the target shows the domain running**: on the target these commands would cut the network of the running guest. Take the tap names from the target's `virsh domiflist DOMAIN` (the names are the same on both nodes) or from `ovs-vsctl list-ports br-int` with the `tap-mini-` prefix:

   ```bash
   sudo ovs-vsctl --if-exists del-port TAP
   sudo ip link delete TAP
   ```

2. On the **target** node, for each tap of the domain (`sudo virsh -c qemu:///system domiflist DOMAIN`), set the four values that the worker sets when it binds a port. `LOGICAL_PORT` is `logical_port_name` of the port and `MAC` its `mac_address` (`cloudx port show PORT_ID`; the ports of the server are `port_id` and `extra_port_ids` in `cloudx server show web-1`), and `SERVER_ID` is the server id:

   ```bash
   sudo ovs-vsctl set Interface TAP external_ids:iface-id=LOGICAL_PORT
   sudo ovs-vsctl set Interface TAP external_ids:attached-mac=MAC
   sudo ovs-vsctl set Interface TAP external_ids:vm-id=SERVER_ID
   sudo ovs-vsctl set Interface TAP external_ids:iface-status=active
   ```

   (`network_manager.set_tap_binding`; if a tap is missing from `br-int`, add it first with `sudo ovs-vsctl --may-exist add-port br-int TAP`.)

3. Fix the server record with the finish call above, but `"result": "success"` and `"target_host": "TARGET"` in the body (TARGET is the compute node name of the target): the server becomes `ACTIVE` on the target and the claim stays there. Never send `"error"` for a guest that runs on the target: the server would become `ACTIVE` with the source as its host, and the target's reconciler would then destroy the moved guest.

4. Optional, on the **source** node only (the target uses the same path for the guest): the source still holds the instance directory of the server (`/var/lib/cloudx/compute/instances/SERVER_ID/`) and, for an LVM volume that the source reached over iSCSI, an iSCSI session. Remove them by hand once you are sure the guest runs on the target.

*The source node is dead.* Force it down (`PUT /os-services/NODE` with `{"forced_down": true}`) and evacuate the server (`POST /servers/{ref}/evacuate`, admin or the internal token): evacuate accepts a `MIGRATING` server once its source is forced down, and builds it again from its volumes on another node. **First check on the target of the live migration that the guest does not run there** (`virsh list`): if it does, the guest survived the move, and an evacuation would start a second copy on the same disks. Settle it with the steps for "the domain runs on the target" instead (skip step 1 and 4, the source is gone).

### Other limits

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
| `live migration failed: ... Host key verification failed` or `Permission denied (publickey)` | The ssh trust is missing or the node name differs from the one in `known_hosts` and `config`. Run `cloudx-setup-migration-ssh` (or `scripts/setup-migration-ssh.sh` from a source checkout) again with the names used in `live_migration_host`. If the host key of a node changed (a reinstalled node), the script does not replace it, because it skips peers that are already in `known_hosts`: on each affected node run `sudo ssh-keygen -R NODE -f /root/.ssh/known_hosts` first, then the script. |
| `live migration failed: ... Connection refused` or a timeout on port 49152-49215 | The memory ports are blocked between the nodes. |
| The migration fails at once with an unknown method error | The target node still runs an older worker. Upgrade the compute nodes before compute-api; with an older worker on the source node the server can stay `MIGRATING` (restart that worker, see above). |
| `server ... cannot be live-migrated (needs ACTIVE)` | The server is not `ACTIVE`, or another migration is running. |
| `... have the same iSCSI initiator name ...` | Two compute nodes share `/etc/iscsi/initiatorname.iscsi` (cloned from one template). Give one a new name (prerequisite 3). |
| `server is MIGRATING (a migration is in progress) ...` on start, stop, delete and so on | A migration runs. Wait for it to end; if it never does, see *Settling a server that stays `MIGRATING`*. |
| `drain` exits non-zero but every row says `skipped` | These servers have a local LV on the node, or are `PAUSED`, `SUSPENDED` and so on. Fix them one by one or accept that they stay. |
