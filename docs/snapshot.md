# Snapshot & Backup

CloudX supports two forms of data protection: **Volume Snapshot** (COW on the storage backend) and **Volume Backup** (a full export to MinIO/S3 object storage).

## Volume Snapshot (Backend-Level)

Volume snapshots are handled natively by the storage backend:
- **LVM backend**: LVM COW (copy-on-write) snapshot at `/dev/VG/snap-xxx`.
- **Ceph backend**: Ceph RBD snapshot at `rbd:pool/image@snap-xxx`.

```bash
cloudx snapshot list
cloudx volume snapshot vol1 --name snap1
cloudx volume create vol2 --size 10 --snapshot snap1
```

## Volume Backup (MinIO Object Storage)

CloudX backs up block storage volumes:
- It exports the full volume (`qemu-img` / `rbd export`) and uploads the raw file to the MinIO bucket `cloudx-backups`.
- It survives even a total failure of the physical storage node.

```bash
cloudx volume backup vol1 --name backup-20260831
cloudx backup list
cloudx backup restore <backup_id>    # restore into a new volume
```

## Server Snapshot

```bash
cloudx server snapshot vm1 --name snap-vm1
cloudx image show snap-vm1
```

Server snapshot flow:

```text
VM -> Root Volume Snapshot -> block-store-worker (qemu-img, qcow2) -> Image API (Image)
```

The command returns at once with the volume snapshot (`creating`) and the name of the image to come. The block-store-worker
of the node that holds the root volume exports the snapshot, converts it to qcow2 and uploads it to image-api as a private
image of the server's project, then removes the volume snapshot it made. Read the progress with `cloudx image list`: the
image shows up as `ACTIVE` when the upload ends. A name that an image already has is refused with 409 before anything is
snapshotted. If the export or the upload fails, no image appears and the volume snapshot stays in `error` with the reason
(`cloudx snapshot list`); delete it with `cloudx snapshot delete`.

The server keeps running while this happens. On LVM the snapshot has a copy-on-write area of 20% of the volume, so a guest
that writes more than that while the export runs invalidates the snapshot and the upload fails; run it again.

The resulting snapshot image can be reused to create new VMs.
