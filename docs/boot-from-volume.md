# Boot From Volume

The project now uses a volume-first architecture. Every VM boots from a root volume. There is no ephemeral root disk and no direct boot from an image.

Default flow:

```text
Image -> Root Volume -> VM
```

Example:

```bash
compute server create vm1 --image cirros --flavor tiny --network private-net
compute server show vm1
block-store volume show <root_volume_id>
```

When a server is created from an image, compute-api asks block-store to create a bootable volume from that image. Compute-worker then uses that volume's path as the libvirt boot disk.

Boot from an existing volume:

```bash
block-store volume create ubuntu-root --size 10 --image ubuntu
compute server create vm2 --boot-volume ubuntu-root --flavor small --network private-net
```

Boot-volume validation:

- `bootable=true`
- `status=available`

Delete policy:

```bash
compute server delete vm1                    # the root volume is deleted too
compute server delete vm1 --keep-root-volume # the root volume is kept
```
