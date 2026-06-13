# OVN ACL Mapping

Nama OVN port group dibuat dari ID security group dan disanitasi oleh networking-api. Endpoint debug menampilkan nama aktual:

```bash
networking security-group ovn-show web
```

Contoh translasi ingress TCP 22:

```text
direction: to-lport
priority: 2002
match: outport == @<port-group> && ip4 && tcp && tcp.dst == 22 && ip4.src == 0.0.0.0/0
action: allow-related
```

Contoh egress allow all IPv4:

```text
direction: from-lport
priority: 2001
match: inport == @<port-group> && ip4 && ip4.dst == 0.0.0.0/0
action: allow-related
```

Default ingress drop selalu ditambahkan pada port group:

```text
direction: to-lport
priority: 1001
match: outport == @<port-group> && ip4
action: drop
```

Implementasi ini belum mencakup seluruh fitur Neutron security group, tetapi sudah memakai primitive OVN yang sama: Port Group dan ACL.
