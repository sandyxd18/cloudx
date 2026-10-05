# Snapshot & Backup

CloudX mendukung dua bentuk proteksi data: **Volume Snapshot** (COW di storage backend) dan **Volume Backup** (ekspor penuh ke object storage MinIO/S3).

## Volume Snapshot (Backend-Level)

Volume snapshot diproses secara native pada storage backend:
- **LVM Backend**: LVM COW (Copy-On-Write) snapshot di `/dev/VG/snap-xxx`.
- **Ceph Backend**: Ceph RBD snapshot di `rbd:pool/image@snap-xxx`.

```bash
cloudx snapshot list
cloudx volume snapshot vol1 --name snap1
cloudx volume create vol2 --size 10 --snapshot snap1
```

## Volume Backup (Object Storage MinIO)

Di Fase 5, CloudX mengimplementasikan Block storage volume backup:
- Meng-export volume penuh (`qemu-img` / `rbd export`) dan me-upload file mentah ke MinIO bucket `cloudx-backups`.
- Bertahan meskipun node storage fisik mengalami kegagalan total.

```bash
cloudx volume backup vol1 --name backup-20260831
cloudx backup list
cloudx backup restore <backup_id>    # restore ke volume baru
```

## Server Snapshot

```bash
cloudx server snapshot vm1 --name snap-vm1
cloudx image show snap-vm1
```

Flow server snapshot:

```text
VM -> Root Volume Snapshot -> Image API (Image)
```

Image hasil snapshot dapat digunakan kembali untuk membuat VM baru.
