# OVN ACL Mapping

The OVN port group name is derived from the security group ID and sanitised by networking-api. The debug endpoint shows the actual name:

```bash
networking security-group ovn-show web
```

Example translation of ingress TCP 22:

```text
direction: to-lport
priority: 2002
match: outport == @<port-group> && ip4 && tcp && tcp.dst == 22 && ip4.src == 0.0.0.0/0
action: allow-related
```

Example of egress allow-all IPv4:

```text
direction: from-lport
priority: 2001
match: inport == @<port-group> && ip4 && ip4.dst == 0.0.0.0/0
action: allow-related
```

A default ingress drop is always added to the port group:

```text
direction: to-lport
priority: 1001
match: outport == @<port-group> && ip4
action: drop
```

This implementation does not yet cover every Networking security group feature, but it already uses the same OVN primitives: Port Group and ACL.
