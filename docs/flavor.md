# Flavor

Flavor is owned by `compute-api`, matching the Nova-like boundary. `image-api` only stores OS image metadata and bytes.

Fields:

- `id`
- `name`
- `vcpus`
- `ram_mb`
- `disk_gb`
- `ephemeral_gb`
- `swap_mb`
- `is_public`
- `created_at`
- `updated_at`

Default bootstrap:

- `tiny`: 1 vCPU, 512 MB RAM, 5 GB disk
- `small`: 1 vCPU, 1024 MB RAM, 10 GB disk
- `medium`: 2 vCPU, 2048 MB RAM, 20 GB disk

Commands:

```bash
compute flavor create NAME --vcpus 1 --ram 1024 --disk 10
compute flavor list
compute flavor show NAME
compute flavor delete NAME
```

Server create requires `--flavor`. Compute stores a snapshot on the server so VM provisioning remains stable even if a flavor is later deleted or changed in a future update.
