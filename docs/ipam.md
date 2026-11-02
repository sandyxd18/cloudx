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

A network can have an IPv4 subnet, an IPv6 subnet or both. A port gets an IPv4 address when the network has an IPv4 subnet. IPv6 is opt-in there: ask for it with `--ipv6` on `cloudx server create` (`"ipv6": true` in the create request), which gives every network of the server its IPv6 address and refuses the request, before anything is built, if one of them has no IPv6 subnet. A port made by hand asks with `fixed_ips` naming the IPv6 subnet (and the IPv4 one, to choose it; without it the first IPv4 subnet is used). The address is allocated and checked like an IPv4 address. A network with no IPv4 subnet gives its ports an IPv6 address without being asked, since there is nothing else to give; such a port has no `fixed_ip`, its address is `ipv6_address`, and that is what the server shows in `cloudx server list`.

```bash
cloudx network create v6net
cloudx subnet create v6sub --network v6net --cidr fd42:7:7::/64          # the version follows the CIDR; no gateway needed unless a router will serve it
cloudx server create vm6 --image cirros --network v6net
cloudx server create vm46 --image cirros --network dualnet --ipv6   # a network with both subnets: IPv4 and IPv6
```

The address and prefix reach the guest through the config drive (`network-config`) and `network_data.json`, as static configuration. A router port on an IPv6 subnet also sends router advertisements, so a guest that is not configured by cloud-init learns the on-link prefix and its default router from them, and its address from DHCPv6 (a DHCPv6 client alone gets a /128 and no route). The advertisement follows the subnet's `--ipv6-ra-mode` (`--ipv6-address-mode` if that is not given, else `dhcpv6-stateful`): `dhcpv6-stateful` sets the managed flag, `dhcpv6-stateless` and `slaac` make the guest build its own address from the prefix, derived from its MAC (the modified EUI-64 address, RFC 4291). On a subnet with one of those two `--ipv6-address-mode` values CloudX allocates that same address to the port, so the port, the server record, the OVN addresses and the guest agree on a single address; `subnet create` refuses such a mode on a prefix that is not a /64, since the interface id is 64 bits. In the rare case that the address is taken, the port gets another MAC. An address named in `fixed_ips` is kept as given, and the guest then has two. A port made before this was added keeps its sequential address next to the one the guest builds; make it again to get the EUI-64 one. Router ports made before this was added get the advertisements when networking-api starts. A subnet with no router has none, and a security group that drops ICMPv6 drops them. The advertisement also names a DNS server (RDNSS, RFC 8106): the first IPv6 address in the subnet's `--dns`, which is what a `slaac` guest without cloud-init can learn a resolver from. OVN takes one RDNSS server per router port, so a second IPv6 server is not advertised, an IPv4 one never is, and a subnet with no IPv6 `--dns` (the default: IPv6 subnets get none) advertises none. The router port takes its address from the subnet's gateway, so `router add-interface` on a subnet made without `--gateway` is refused with 400; a subnet cannot be edited, so create it again with `--gateway`. `--ipv6-ra-mode` and `--ipv6-address-mode` on `subnet create` also decide whether OVN gets a DHCPv6 options row (`slaac` for both: none).

Commands:

```bash
networking port list
networking port show PORT
networking port delete PORT
```
