# Single-Node Lab Scope

The real VM backend is intentionally scoped to one lab host:

- no scheduler
- no placement inventory
- no live migration
- no multi-node networking tunnel decisions

Resource names are prefixed so they can be extended later:

```text
mini-os-<server_name>-<short_id>
tap-mini-<6-char-id>
```

For now, `compute-api` sends all libvirt work to one `compute-worker`.
