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

Commands:

```bash
networking port list
networking port show PORT
networking port delete PORT
```
