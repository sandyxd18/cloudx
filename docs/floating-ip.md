# Floating IP

Floating IP follows the CloudX model: the VM keeps its private fixed IP, and OVN creates a NAT mapping between a provider-network IP and that private IP.

Normal workflow:

```bash
networking network show public-net
networking router create router1
networking router add-interface router1 --subnet private-subnet
networking router set-gateway router1 public-net

networking floating-ip create public-net
networking floating-ip associate 192.168.56.100 --server vm1 --router router1
networking floating-ip disassociate 192.168.56.100
networking floating-ip delete 192.168.56.100
```

You can associate by port instead of server:

```bash
networking floating-ip associate 192.168.56.100 --port <port-id> --router router1
```

Association creates an OVN logical router NAT rule:

```text
type=dnat_and_snat external_ip=<floating-ip> logical_ip=<vm-private-ip>
```

In a single-node lab, the floating IP is usually reachable only from the host or the host-only/provider network. Example:

```text
Laptop host -> 192.168.56.100 -> OVN NAT -> 10.10.0.5
```

The floating IP is not configured inside the VM.
