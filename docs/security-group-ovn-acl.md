# Security Group OVN ACL

Security groups are implemented as a small OVN ACL subset, not full OpenStack security groups.

```bash
networking security-group create web --description "Allow web and ssh"
networking security-group rule create web --direction ingress --protocol tcp --port 22 --remote-ip 0.0.0.0/0
networking security-group rule create web --direction ingress --protocol tcp --port 80 --remote-ip 0.0.0.0/0
networking port set-security-group <port> --security-group web
```

When a security group is attached, ingress defaults to deny except established traffic and matching allow rules. Egress is allowed by default.
