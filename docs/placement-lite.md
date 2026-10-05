# Placement Lite

Placement-lite is not the full CloudX Placement. It is only a single-node inventory that prevents creating a server when capacity is clearly insufficient.

Command:

```bash
compute host show
```

The output contains:

- `total_vcpu`, `used_vcpu`, `free_vcpu`
- `total_ram_mb`, `used_ram_mb`, `free_ram_mb`
- `total_disk_gb`, `used_disk_gb`, `free_disk_gb`

Env override:

```env
PLACEMENT_TOTAL_VCPU=8
PLACEMENT_TOTAL_RAM_MB=16384
PLACEMENT_TOTAL_DISK_GB=200
```

If a flavor exceeds the free capacity, compute-api returns an `Insufficient resources` error. There is no multi-node scheduler.
