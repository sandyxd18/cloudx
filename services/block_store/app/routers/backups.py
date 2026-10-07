from fastapi import APIRouter, HTTPException
from shared.auth_middleware import CurrentUser
from sqlalchemy import select

from .. import clients
from ..db import DB
from ..lookups import can_access, require_volume
from ..models import Volume, VolumeBackup, new_id
from ..schemas import BackupIn, RestoreIn
from ..serializers import obj_to_dict
from ..service import pick_lvm_host

router = APIRouter()


@router.post("/volumes/{ref}/backup", status_code=202)
def backup_volume(ref: str, payload: BackupIn, current: CurrentUser, db: DB):
    """Create a full-data backup of a volume to object storage (MinIO/S3).

    Unlike a snapshot (which stays on the same backend), a backup exports the
    entire volume image and uploads it to the backup bucket, so it survives
    loss of the storage node — mirroring Swift/S3 backup driver.
    """
    volume = require_volume(db, ref, current)
    backup_id = new_id()
    name = payload.name or f"backup-{volume.name}"
    object_key = f"volumes/{volume.project_id or 'default'}/{backup_id}.raw"

    backup = VolumeBackup(
        id=backup_id,
        name=name,
        volume_id=volume.id,
        size_gb=volume.size_gb,
        status="creating",
        backend=volume.backend,
        object_key=object_key,
        project_id=volume.project_id or current.get("project_id"),
    )
    db.add(backup)
    db.commit()
    db.refresh(backup)

    clients.cast_to_worker("backup_volume", {
        "backup_id":   backup_id,
        "volume_id":   volume.id,
        "volume_path": volume.path,
        "backend":     volume.backend,
        "size_gb":     volume.size_gb,
        "object_key":  object_key,
    })
    return _backup_to_dict(backup)


def _backup_to_dict(b: VolumeBackup) -> dict:
    return {c.name: getattr(b, c.name) for c in b.__table__.columns}


@router.get("/backups")
def list_backups(current: CurrentUser, db: DB):
    q = select(VolumeBackup)
    if current.get("role") != "admin":
        q = q.where(VolumeBackup.project_id == current.get("project_id"))
    return {"items": [_backup_to_dict(b) for b in db.scalars(q.order_by(VolumeBackup.created_at.desc())).all()]}


@router.get("/backups/{backup_id}")
def show_backup(backup_id: str, current: CurrentUser, db: DB):
    b = db.get(VolumeBackup, backup_id)
    if not b or not can_access(b.project_id, current):
        raise HTTPException(status_code=404, detail=f"backup '{backup_id}' not found")
    return _backup_to_dict(b)


@router.delete("/backups/{backup_id}", status_code=202)
def delete_backup(backup_id: str, current: CurrentUser, db: DB):
    b = db.get(VolumeBackup, backup_id)
    if not b or not can_access(b.project_id, current):
        raise HTTPException(status_code=404, detail=f"backup '{backup_id}' not found")
    b.status = "deleting"
    db.commit()
    clients.cast_to_worker("delete_backup", {
        "backup_id":  backup_id,
        "object_key": b.object_key,
    })
    return {"status": "deleting", "id": backup_id}


@router.post("/backups/{backup_id}/restore", status_code=202)
def restore_backup(backup_id: str, payload: RestoreIn, current: CurrentUser, db: DB):
    """Restore a backup into a new volume."""
    b = db.get(VolumeBackup, backup_id)
    if not b or not can_access(b.project_id, current):
        raise HTTPException(status_code=404, detail=f"backup '{backup_id}' not found")
    if b.status != "available":
        raise HTTPException(status_code=409, detail=f"backup not available (status={b.status})")

    new_vol_id = new_id()
    vol = Volume(
        id=new_vol_id,
        name=payload.name or f"restore-{b.name}",
        size_gb=b.size_gb,
        status="creating",
        backend=b.backend,
        host=pick_lvm_host(db) if b.backend == "lvm" else None,
        volume_type=payload.volume_type,
        project_id=current.get("project_id"),
    )
    db.add(vol)
    db.commit()
    db.refresh(vol)

    clients.cast_to_worker("restore_backup", {
        "backup_id":  backup_id,
        "object_key": b.object_key,
        "volume_id":  new_vol_id,
        "backend":    b.backend,
        "size_gb":    b.size_gb,
    })
    return obj_to_dict(vol)
