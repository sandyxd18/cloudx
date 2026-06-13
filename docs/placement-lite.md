# Placement Lite

Placement-lite bukan OpenStack Placement penuh. Ini hanya inventory single-node untuk mencegah create server saat kapasitas jelas tidak cukup.

Command:

```bash
compute host show
```

Output berisi:

- `total_vcpu`, `used_vcpu`, `free_vcpu`
- `total_ram_mb`, `used_ram_mb`, `free_ram_mb`
- `total_disk_gb`, `used_disk_gb`, `free_disk_gb`

Env override:

```env
PLACEMENT_TOTAL_VCPU=8
PLACEMENT_TOTAL_RAM_MB=16384
PLACEMENT_TOTAL_DISK_GB=200
```

Jika flavor melebihi kapasitas bebas, compute-api mengembalikan error `Insufficient resources`. Tidak ada scheduler multi-node.
