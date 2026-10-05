# Root Volume

Every server has `root_volume_id`, `boot_source`, and `bootable` in the output of `compute server show`.

A root volume is created automatically when a server is created with `--image`:

```bash
compute server create vm1 --image cirros --flavor tiny --network private-net
compute server show vm1
```

The root volume is created in block-store with this metadata:

- `bootable=true`
- `image_id=<image-id>`
- `source_type=image`
- `status=in-use` while the server is running

The worker uses virtio-scsi to keep volume hotplug stable. In libvirt the root disk uses target `sda`; subsequent data volumes start at `sdb`.
