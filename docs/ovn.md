# OVN Notes

The lab runs real OVN databases and `ovn-northd`.

## Containers

- `ovn-nb-db`: Northbound OVSDB on TCP port `6641`.
- `ovn-sb-db`: Southbound OVSDB on TCP port `6642`.
- `ovn-northd`: translates northbound logical configuration to southbound flows.
- `ovs-ovn-controller`: starts OVS, creates `br-int`, and runs `ovn-controller`.

The controller container is privileged because OVS needs kernel/network privileges.

## Useful Commands

```bash
docker compose exec networking-api ovn-nbctl --db=tcp:ovn-nb-db:6641 show
docker compose exec ovn-sb-db ovn-sbctl --db=tcp:ovn-sb-db:6642 show
docker compose logs -f ovn-northd
docker compose logs -f ovs-ovn-controller
```

From the installed CLI:

```bash
networking ovn show
```

## Fallback Debugging

If OVN does not start on your host:

- Confirm Docker can run privileged containers.
- Check that ports `6641` and `6642` are not already used.
- Rebuild the OVN image with `docker compose build ovn-nb-db`.
- Inspect `docker compose logs ovn-nb-db ovn-sb-db ovn-northd ovs-ovn-controller`.

The API metadata still lives in PostgreSQL, but networking create/delete operations intentionally fail if `ovn-nbctl` cannot reach the Northbound DB. This keeps the lab honest about whether OVN is actually wired.

## Cleanup

Use the cleanup script to inspect project-owned local leftovers:

```bash
sudo scripts/cleanup-network.sh --dry-run
sudo scripts/cleanup-network.sh
```

The script is conservative. It deletes resources with `mini-os-` naming and only deletes `br-int` or `br-ex` when those bridges are explicitly marked as owned by mini OpenStack lab.
