# OVN DHCP and IPAM

`networking-api` allocates fixed IPs from subnet pools and creates OVN DHCP options when DHCP is enabled.

```bash
networking network create private-net
networking subnet create private-subnet --network private-net --cidr 10.10.0.0/24 --gateway 10.10.0.1 --dhcp
networking port create port1 --network private-net
networking port show port1
```

Allocated addresses skip the subnet gateway and existing port IPs. Ports are configured with `addresses="<mac> <ip>"` and `dhcpv4_options=<uuid>`.
