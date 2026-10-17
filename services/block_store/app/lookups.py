from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import STORAGE_CEPH_RBD_DRIVER, STORAGE_LVM_DRIVER, STORAGE_NFS_DRIVER
from shared.domain import can_see
from .models import ConsistencyGroup, Snapshot, Volume, VolumeType


def get_volume(db: Session, ref: str):
    return db.get(Volume, ref) or db.scalar(select(Volume).where(Volume.name == ref))


def can_access(project_id: str | None, current: dict | None) -> bool:
    """Admins (and internal callers, current=None) see everything; others only their own project (or unowned rows)."""
    return current is None or not project_id or can_see(current, project_id)


def require_volume(db: Session, ref: str, current: dict | None = None) -> Volume:
    volume = get_volume(db, ref)
    if not volume or not can_access(volume.project_id, current):
        raise HTTPException(status_code=404, detail=f"volume '{ref}' not found")
    return volume


def get_snapshot(db: Session, ref: str):
    return db.get(Snapshot, ref) or db.scalar(select(Snapshot).where(Snapshot.name == ref))


def require_snapshot(db: Session, ref: str, current: dict | None = None) -> Snapshot:
    snapshot = get_snapshot(db, ref)
    if snapshot and current is not None:  # snapshots have no project column: they follow their volume's project
        vol = db.get(Volume, snapshot.volume_id)
        if vol and not can_access(vol.project_id, current):
            snapshot = None
    if not snapshot:
        raise HTTPException(status_code=404, detail=f"snapshot '{ref}' not found")
    return snapshot


def _resolve_volume_type(db: Session, type_name: str | None) -> VolumeType:
    if type_name:
        vt = db.scalar(select(VolumeType).where(VolumeType.name == type_name))
        if not vt:
            available = db.scalars(select(VolumeType.name)).all()
            hint = f"Available types: {', '.join(available)}" if available else "No volume types have been created yet."
            raise HTTPException(
                status_code=400,
                detail=f"Volume type '{type_name}' not found. {hint}"
            )
        return vt

    vt = db.scalar(select(VolumeType).order_by(VolumeType.name))
    if not vt:
        raise HTTPException(
            status_code=400,
            detail=(
                "No volume types are configured. "
                "An admin must create at least one volume type before creating volumes. "
                "Example: cloudx volume-type create gp2 --backend lvm --disk-type sata_ssd --max-iops 3000 --max-throughput 125"
            ),
        )
    return vt


def _validate_backend(vt: VolumeType) -> None:
    if vt.backend == "lvm" and not STORAGE_LVM_DRIVER:
        raise HTTPException(status_code=400, detail="LVM driver is disabled (STORAGE_LVM_DRIVER=false)")
    if vt.backend == "ceph" and not STORAGE_CEPH_RBD_DRIVER:
        raise HTTPException(status_code=400, detail="Ceph driver is disabled (STORAGE_CEPH_RBD_DRIVER=false)")
    if vt.backend == "nfs" and not STORAGE_NFS_DRIVER:
        raise HTTPException(status_code=400, detail="NFS driver is disabled (STORAGE_NFS_DRIVER=false)")


def next_target_device(db: Session, server_id: str) -> str:
    used = set(
        item for item in db.scalars(
            select(Volume.target_device).where(
                Volume.attached_to_server_id == server_id,
                Volume.target_device.is_not(None),
            )
        ).all()
        if item
    )
    for letter in "bcdefghijklmnopqrstuvwxyz":
        candidate = f"sd{letter}"
        if candidate not in used:
            return candidate
    raise HTTPException(status_code=409, detail="no available disk target devices for server")


def require_cg(db: Session, ref: str) -> ConsistencyGroup:
    cg = db.scalar(select(ConsistencyGroup).where(
        (ConsistencyGroup.id == ref) | (ConsistencyGroup.name == ref)
    ))
    if not cg:
        raise HTTPException(status_code=404, detail=f"Consistency group '{ref}' not found")
    return cg
