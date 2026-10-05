# Multi-Network VM

The multi-network phase uses ports that were already created in networking-api. Compute-worker creates an extra tap, binds it to OVS with the logical port's `iface-id`, and then hotplugs the interface into libvirt.

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
