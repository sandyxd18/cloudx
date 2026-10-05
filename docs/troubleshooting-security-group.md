# Troubleshooting Security Group

Check the translation of a security group into OVN:

```bash
networking security-group show web
networking security-group ovn-show web
networking port ovn-show <port-id>
```

Check directly in the OVN NB DB:

```bash
docker compose exec networking-api ovn-nbctl --db=tcp:ovn-nb-db:6641 list port_group
docker compose exec networking-api ovn-nbctl --db=tcp:ovn-nb-db:6641 --type=port-group acl-list <port-group>
```

If SSH or ping is blocked, make sure the ingress rule exists and the port is still a member of the port group. If `port_security_enabled=false`, the security group is not applied to that port.

Clean up orphan port groups/ACLs left by the lab:

```bash
scripts/cleanup-network.sh --dry-run
sudo scripts/cleanup-network.sh --force
```
