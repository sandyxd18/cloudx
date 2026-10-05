from datetime import datetime

from .models import Snapshot, Volume

    # Ensure quota table exists (create_all handles this via ORM model)
    # The VolumeQuota model is registered in Base, so create_all above covers it.


# ── DB helpers ─────────────────────────────────────────────────────────────────

def obj_to_dict(obj):
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}


def _fmt_ts(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000000")


def volume_to_os_dict(volume: Volume) -> dict:
    """Serialize a Volume to an OpenStack-compatible dict."""
    attachments = []
    if volume.attached_to_server_id:
        attachments = [{
            "id": volume.id,
            "volume_id": volume.id,
            "server_id": volume.attached_to_server_id,
            "host_name": "single-node",
            "device": f"/dev/{volume.target_device or 'vda'}",
            "attached_at": _fmt_ts(volume.created_at),
            "attachment_id": volume.id,
        }]

    return {
        "attachments": attachments,
        "availability_zone": "default",
        "backup_id": None,
        "bootable": str(volume.bootable).lower() == "true" or volume.bootable is True,
        "cluster_name": None,
        "consumes_quota": True,
        "created_at": _fmt_ts(volume.created_at),
        "description": "",
        "encrypted": False,
        "group_id": None,
        "id": volume.id,
        "multiattach": bool(volume.multiattach),
        "name": volume.name or "",
        "os-vol-host-attr:host": f"{volume.host or 'single-node'}@{volume.backend or 'lvm'}#{volume.backend or 'lvm'}",
        "os-vol-mig-status-attr:migstat": None,
        "os-vol-mig-status-attr:name_id": None,
        "os-vol-tenant-attr:tenant_id": volume.project_id,
        "properties": {},
        "provider_id": None,
        "replication_status": None,
        "size": volume.size_gb,
        "snapshot_id": volume.snapshot_id,
        "source_volid": volume.source_volume_id,
        "status": volume.status,
        "type": volume.volume_type or volume.backend or "lvm",
        "updated_at": _fmt_ts(volume.created_at),
        "user_id": None,
        "volume_image_metadata": {},
        # Internal fields kept for backward compat
        "size_gb": volume.size_gb,
        "path": volume.path,
        "backend": volume.backend,
        "host": volume.host,
        "image_id": volume.image_id,
        "target_device": volume.target_device,
        "attached_to_server_id": volume.attached_to_server_id,
        "project_id": volume.project_id,
        "error_message": volume.error_message,
    }


def snapshot_to_os_dict(snapshot: Snapshot) -> dict:
    """Serialize a Snapshot to an OpenStack-compatible dict."""
    return {
        "created_at": _fmt_ts(snapshot.created_at),
        "description": "",
        "id": snapshot.id,
        "name": snapshot.name or "",
        "size": snapshot.size_gb,
        "status": snapshot.status,
        "updated_at": _fmt_ts(snapshot.created_at),
        "volume_id": snapshot.volume_id,
        # Internal
        "size_gb": snapshot.size_gb,
        "path": snapshot.path,
        "backend": snapshot.backend,
    }
