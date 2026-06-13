# Security Group

Security group di lab ini meniru model Neutron/OVN secara sederhana:

- SecurityGroup menjadi OVN Port Group.
- SecurityGroupRule menjadi OVN ACL pada port group.
- Port menjadi member port group saat `port_security_enabled=true`.

Default security group dibuat saat networking-api startup. Env berikut mengatur usability default lab:

```env
DEFAULT_SECURITY_GROUP_ALLOW_SSH=true
DEFAULT_SECURITY_GROUP_ALLOW_ICMP=true
```

Jika port dibuat tanpa security group, `default` dipakai otomatis. Ingress default di-drop, egress IPv4 default di-allow. Rule allow memakai `allow-related` untuk stateful behavior OVN.

Workflow:

```bash
networking security-group create web --description "Allow web and ssh"
networking security-group rule create web --direction ingress --protocol tcp --port 22 --remote-ip 0.0.0.0/0
networking security-group rule create web --direction ingress --protocol icmp --remote-ip 0.0.0.0/0
networking security-group rule list web
networking security-group ovn-show web

compute server create vm1 --image cirros --flavor tiny --network private-net --key-name mykey --security-group web

networking port set-security-group <port-id> --security-group web
networking port clear-security-groups <port-id>
networking port security-disable <port-id>
networking port security-enable <port-id>
networking port ovn-show <port-id>
```

`port security-disable` hanya untuk lab/admin/debug. Saat disabled, port dilepas dari OVN port group security group.
