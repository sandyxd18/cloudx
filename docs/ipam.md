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

A network can have an IPv4 subnet, an IPv6 subnet or both. A port gets one address from each family the network has a subnet for, unless the request names addresses (`fixed_ip`, or `fixed_ips` with subnets): then it gets what was asked for. On a network with only an IPv6 subnet the port has no `fixed_ip`; its address is `ipv6_address`, and that is what the server shows in `cloudx server list`.

```bash
cloudx network create v6net
cloudx subnet create v6sub --network v6net --cidr fd42:7:7::/64          # the version follows the CIDR; no gateway needed
cloudx server create vm6 --image cirros --network v6net
cloudx security-group rule add default --ethertype IPv6 --protocol icmp   # the default group only has IPv4 rules
```

The address and prefix reach the guest through the config drive (`network-config`) and `network_data.json`, as static configuration. There are no router advertisements, so a guest that is not configured by cloud-init does not learn its address by itself. `--ipv6-ra-mode` and `--ipv6-address-mode` on `subnet create` decide whether OVN gets a DHCPv6 options row (`slaac` for both: none).

Commands:

```bash
networking port list
networking port show PORT
networking port delete PORT
```
