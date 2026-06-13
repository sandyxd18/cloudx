# Multi-Network VM

Multi-network fase ini memakai port yang sudah dibuat di networking-api. Compute-worker membuat tap tambahan, bind ke OVS dengan `iface-id` logical port, lalu hotplug interface ke libvirt.

Contoh:

```bash
networking network create net2
networking subnet create subnet2 --network net2 --cidr 10.20.0.0/24 --gateway 10.20.0.1 --dhcp
networking port create vm1-net2 --network net2
compute server add-port vm1 vm1-net2
compute server show vm1
compute server remove-port vm1 vm1-net2
```

Primary port tetap berada pada `port_id`; port tambahan disimpan di `extra_port_ids`.
