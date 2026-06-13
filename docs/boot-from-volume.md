# Boot From Volume

Project sekarang memakai volume-first architecture. Semua VM boot dari root volume. Tidak ada ephemeral root disk dan tidak ada boot langsung dari image.

Flow default:

```text
Image -> Root Volume -> VM
```

Contoh:

```bash
compute server create vm1 --image cirros --flavor tiny --network private-net
compute server show vm1
block-store volume show <root_volume_id>
```

Saat server dibuat dari image, compute-api meminta block-store membuat volume bootable dari image tersebut. Compute-worker kemudian memakai path volume itu sebagai disk boot libvirt.

Boot dari volume existing:

```bash
block-store volume create ubuntu-root --size 10 --image ubuntu
compute server create vm2 --boot-volume ubuntu-root --flavor small --network private-net
```

Validasi boot-volume:

- `bootable=true`
- `status=available`

Delete policy:

```bash
compute server delete vm1                    # root volume ikut dihapus
compute server delete vm1 --keep-root-volume # root volume disimpan
```
