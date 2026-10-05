# Security Group

Security groups in this lab imitate the Networking/OVN model in a simple way:

- A SecurityGroup becomes an OVN Port Group.
- A SecurityGroupRule becomes an OVN ACL on the port group.
- A port becomes a member of the port group when `port_security_enabled=true`.

The default security group is created when networking-api starts. The following env settings control the lab's default usability:

```env
DEFAULT_SECURITY_GROUP_ALLOW_SSH=true
DEFAULT_SECURITY_GROUP_ALLOW_ICMP=true
```

If a port is created without a security group, `default` is used automatically. Ingress is dropped by default and IPv4 egress is allowed by default. Allow rules use `allow-related` for OVN's stateful behaviour.

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

`port security-disable` is only for lab/admin/debug use. When it is disabled, the port is removed from the security group's OVN port group.
