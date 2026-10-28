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
VM -> Root Volume Snapshot -> Image API (Image)
```

The resulting snapshot image can be reused to create new VMs.
