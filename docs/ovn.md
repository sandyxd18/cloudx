# OVN Notes

CloudX runs real OVN databases and `ovn-northd`, installed from the Ubuntu `ovn-central` package on the controller and `ovn-host` on compute nodes.

## Processes

- `ovn-central` (controller): the Northbound OVSDB on TCP port `6641`, the Southbound OVSDB on TCP port `6642`, and `ovn-northd`, which translates the northbound logical configuration to southbound flows.
- `ovn-controller` (every compute and gateway node, from `ovn-host`): connects to the Southbound DB, creates and manages the flows on `br-int`.

Open vSwitch (`openvswitch-switch`) runs on every node that hosts VMs or gateways.

## Useful Commands

```bash
sudo ovn-nbctl --db=tcp:<controller-ip>:6641 show
sudo ovn-sbctl --db=tcp:<controller-ip>:6642 show
sudo journalctl -u ovn-northd -f             # controller
sudo journalctl -u ovn-controller -f         # compute and gateway nodes
```

On the controller itself the `--db` option can be dropped; `ovn-nbctl show` talks to the local socket.

From the installed CLI:

```bash
networking ovn show
```

## Fallback Debugging

If OVN does not start on your host:

- Check that ports `6641` and `6642` are listening on the controller (`ss -ltn | grep -E '6641|6642'`) and are not used by another process. The listeners are configured in [SETUP.md](../SETUP.md) section 2.4.
- Check the services: `systemctl status ovn-central ovn-northd ovn-ovsdb-server-nb ovn-ovsdb-server-sb` on the controller, `systemctl status ovn-host openvswitch-switch` on compute nodes.
- On a compute node, `sudo ovs-vsctl get open_vswitch . external_ids` must show `ovn-remote` pointing at the controller and a unique `system-id`; `cloudx-setup-compute` sets both.
- Inspect `sudo journalctl -u ovn-central -u ovn-northd -u ovn-controller`.

The API metadata still lives in PostgreSQL, but networking create/delete operations intentionally fail if `ovn-nbctl` cannot reach the Northbound DB. This keeps the lab honest about whether OVN is actually wired.

## Cleanup

Use the cleanup script to inspect project-owned local leftovers:

```bash
sudo scripts/cleanup-network.sh --dry-run
sudo scripts/cleanup-network.sh
```

The script is conservative. It deletes resources with `cloudx-` naming and only deletes `br-int` or `br-ex` when those bridges are explicitly marked as owned by CloudX lab.
