# Single-Node Lab Scope

> **Out of date.** This describes the first single-host VM backend. CloudX now has a scheduler and a Placement service. For one machine see [quickstart.md](quickstart.md).

The real VM backend is intentionally scoped to one lab host:

- no scheduler
- no placement inventory
- no live migration
- no multi-node networking tunnel decisions

Resource names are prefixed so they can be extended later:

```text
cloudx-<server_name>-<short_id>
tap-mini-<6-char-id>
```

For now, `compute-api` sends all libvirt work to one `compute-worker`.
