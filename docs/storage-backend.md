# Image Storage Backends

MinIO has two main roles in CloudX:
1. **CloudX Image Storage**: stores OS image blobs (`IMAGE_STORAGE_BACKEND=minio`).
2. **Block Store Volume & PostgreSQL Backups (Phase 5)**: stores the automatic daily database backup (23:00 Asia/Jakarta) and block storage volume exports in the `cloudx-backups` bucket.

## File Backend

```env
IMAGE_STORAGE_BACKEND=file
IMAGE_STORE_PATH=/var/lib/cloudx/images
```

This is the default. Image files are written to the directory set by `image_store_path` (`/var/lib/cloudx/images`) on the node that runs `image-api` and `image-worker`. PostgreSQL stores metadata including:

- `storage_backend=file`
- `object_name`
- `filename`
- `size`
- `checksum`

## MinIO Backend

```env
IMAGE_STORAGE_BACKEND=minio
MINIO_ENDPOINT=http://minio:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET=cloudx-images
MINIO_SECURE=false
```

CloudX does not install MinIO. Run it yourself (the MinIO binary or package, as a service on the storage node or elsewhere), create the `cloudx-images` bucket, and put the settings above in `/etc/cloudx/image-api/image-api.conf` as lowercase keys (`image_storage_backend = minio`, `minio_endpoint = http://<minio-host>:9000`, and so on). Then restart `cloudx-image-api` and `cloudx-image-worker`. The API still computes SHA256 checksum and size before storing metadata in PostgreSQL.

## API Compatibility

The CLI remains unchanged:

```bash
image upload ubuntu --file ./ubuntu.qcow2 --disk-format qcow2
image show ubuntu
```

`image show` includes `storage_backend` and `object_name` so callers can see where bytes are stored.
