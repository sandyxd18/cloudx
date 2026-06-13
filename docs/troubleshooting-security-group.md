# Troubleshooting Security Group

Cek translasi security group ke OVN:

```bash
networking security-group show web
networking security-group ovn-show web
networking port ovn-show <port-id>
```

Cek langsung di OVN NB DB:

```bash
docker compose exec networking-api ovn-nbctl --db=tcp:ovn-nb-db:6641 list port_group
docker compose exec networking-api ovn-nbctl --db=tcp:ovn-nb-db:6641 --type=port-group acl-list <port-group>
```

Jika SSH atau ping tertutup, pastikan rule ingress ada dan port masih menjadi member port group. Jika `port_security_enabled=false`, security group tidak diterapkan ke port tersebut.

Cleanup orphan port group/ACL milik lab:

```bash
scripts/cleanup-network.sh --dry-run
sudo scripts/cleanup-network.sh --force
```
