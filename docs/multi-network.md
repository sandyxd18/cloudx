# Multi-Network VM

The multi-network phase uses ports that were already created in networking-api. Compute-worker creates an extra tap, binds it to OVS with the logical port's `iface-id`, and then hotplugs the interface into libvirt.

## At creation

`cloudx server create` takes `--network` more than once. The first is the primary network (`network` in the API, the `port_id` port); every other one adds a NIC on that network (`networks` in the API, stored in `extra_port_ids`). All the NICs are plugged when the server is built, and again when it is evacuated:

```bash
cloudx server create web-1 --image ubuntu --flavor small --network app-net --network db-net
```

- A network can be given once. A repeated `--network` is refused (`network app-net is given twice`) before anything is sent, because the API would drop it without a word and the server would have fewer NICs than asked for.
- `--ipv6` applies to every network, and the request is refused if one of them has no IPv6 subnet.
- `--security-group` applies to every port of the server.
- Older releases took `--network` once: a second one was silently ignored (the last value won), and several networks needed the API.

## After creation

Example:

```bash
networking network create net2
networking subnet create subnet2 --network net2 --cidr 10.20.0.0/24 --gateway 10.20.0.1 --dhcp
networking port create vm1-net2 --network net2
compute server add-port vm1 vm1-net2
compute server show vm1
compute server remove-port vm1 vm1-net2
```

The primary port stays in `port_id`; extra ports are stored in `extra_port_ids`.
