# OVS/OVN VM Binding

`networking-api` creates an OVN logical switch port for each compute port. `compute-worker` creates a Linux tap and binds it to `br-int`.

Tap naming:

```text
tap-mini-<6-char-server-id>
```

OVS commands used by the worker:

```bash
ovs-vsctl --may-exist add-port br-int tap-mini-xxx
ovs-vsctl set Interface tap-mini-xxx external_ids:iface-id=<logical_port_name>
ovs-vsctl set Interface tap-mini-xxx external_ids:attached-mac=<mac_address>
ovs-vsctl set Interface tap-mini-xxx external_ids:vm-id=<server_id>
ovs-vsctl set Interface tap-mini-xxx external_ids:iface-status=active
```

Linux interface names are limited to 15 characters, so the `tap-mini-` prefix uses a 6 character server id suffix.

Cleanup only targets project-owned resources with `cloudx-` and `tap-mini-` prefixes.
