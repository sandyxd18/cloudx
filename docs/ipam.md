# IPAM

`networking-api` has simple single-subnet-per-network allocation for the lab.

Subnet metadata:

- `cidr`
- `gateway_ip`
- `allocation_start`
- `allocation_end`
- `dns_nameservers`

When a port is created, networking-api generates:

- MAC address
- fixed IP
- logical switch name
- logical port name
- optional `device_id`
- optional `device_owner`

Compute-created ports use:

```text
device_owner=compute:compute-based
device_id=<server_id>
```

OVN logical switch ports receive:

```text
addresses="<mac> <ip>"
external_ids:device_id=<server_id>
external_ids:device_owner=compute:compute-based
```

## IPv6

A network can have an IPv4 subnet, an IPv6 subnet or both. A port gets an IPv4 address when the network has an IPv4 subnet. IPv6 is opt-in there: ask for it with `fixed_ips` naming both subnets (API only), and it is allocated and checked like an IPv4 address. A network with no IPv4 subnet gives its ports an IPv6 address without being asked, since there is nothing else to give; such a port has no `fixed_ip`, its address is `ipv6_address`, and that is what the server shows in `cloudx server list`.

```bash
cloudx network create v6net
cloudx subnet create v6sub --network v6net --cidr fd42:7:7::/64          # the version follows the CIDR; no gateway needed
cloudx server create vm6 --image cirros --network v6net
```

The address and prefix reach the guest through the config drive (`network-config`) and `network_data.json`, as static configuration. There are no router advertisements, so a guest that is not configured by cloud-init does not learn its address by itself. `--ipv6-ra-mode` and `--ipv6-address-mode` on `subnet create` decide whether OVN gets a DHCPv6 options row (`slaac` for both: none).

Commands:

```bash
networking port list
networking port show PORT
networking port delete PORT
```
