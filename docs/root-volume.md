# Root Volume

Setiap server memiliki `root_volume_id`, `boot_source`, dan `bootable` pada output `compute server show`.

Root volume otomatis dibuat saat create server dengan `--image`:

```bash
compute server create vm1 --image cirros --flavor tiny --network private-net
compute server show vm1
```

Root volume dibuat di block-store dengan metadata:

- `bootable=true`
- `image_id=<image-id>`
- `source_type=image`
- `status=in-use` saat server berjalan

Worker memakai virtio-scsi untuk menjaga hotplug volume tetap stabil. Di libvirt root disk memakai target `sda`; data volume berikutnya mulai dari `sdb`.
