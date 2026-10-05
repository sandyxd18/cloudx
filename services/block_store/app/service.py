import requests
from fastapi import HTTPException
from shared.auth_middleware import CurrentUser
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import clients
from .config import AUTH_API_URL, COMPUTE_API_URL, IMAGE_API_URL, LVM_HOSTS, log
from .db import DB
from .lookups import (
    _resolve_volume_type,
    _validate_backend,
    get_snapshot,
    get_volume,
    require_snapshot,
    require_volume,
)
from .models import Snapshot, Volume, new_id
from .schemas import VolumeInternalIn
from .serializers import obj_to_dict


def _check_auth_volume_quota(project_id: str, size: int, db: Session):
    try:
        quota_resp = clients.service_client.get(f"{AUTH_API_URL}/projects/{project_id}/quota", timeout=5)
        if quota_resp.status_code == 200:
            auth_quota = quota_resp.json()
            max_vol_gb = auth_quota.get("max_volume_gb", 100)
            current_vols = db.scalars(
                select(Volume).where(
                    Volume.project_id == project_id,
                    Volume.status.not_in(["deleting", "error"]),
                )
            ).all()
            used_gb = sum(v.size_gb for v in current_vols)
            if used_gb + size > max_vol_gb:
                raise HTTPException(
                    status_code=413,
                    detail=f"Volume GB quota exceeded: {used_gb + size}/{max_vol_gb} GB limit reached",
                )
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("Auth project volume quota check skipped: %s", exc)


def pick_lvm_host(db: Session, hosts: list[str] | None = None) -> str | None:
    """Storage node with the least allocated GB among the configured LVM hosts (None if none are configured)."""
    hosts = LVM_HOSTS if hosts is None else hosts
    if not hosts:
        return None
    used = dict(db.execute(
        select(Volume.host, func.coalesce(func.sum(Volume.size_gb), 0))
        .where(Volume.host.in_(hosts), Volume.status != "deleting")
        .group_by(Volume.host)
    ).all())
    return min(hosts, key=lambda h: used.get(h, 0))  # min() keeps the first host on ties


def _create_volume_record(db: Session, payload: VolumeInternalIn, project_id: str | None = None, multiattach: bool = False) -> dict:
    if get_volume(db, payload.name):
        raise HTTPException(status_code=409, detail="volume already exists")

    image_ref = payload.image_ref or payload.image_id or getattr(payload, "image", None)
    snapshot_ref = payload.snapshot_id or getattr(payload, "snapshot", None)
    source_vol_ref = payload.source_volume_id or getattr(payload, "source_volume", None)

    source_count = sum(bool(x) for x in (
        image_ref,
        snapshot_ref,
        source_vol_ref,
    ))
    if source_count > 1:
        raise HTTPException(status_code=400, detail="choose only one source: image, snapshot, or source_volume")

    vt = _resolve_volume_type(db, payload.volume_type)
    _validate_backend(vt)
    backend = vt.backend
    host = None  # storage node (LVM only); clones and snapshot restores must stay on the source's node

    volume_id = new_id()
    source_type = payload.source_type
    bootable    = payload.bootable
    snapshot_id = None
    source_volume_id = None

    # Validate sources exist before creating record
    if image_ref:
        source_type = "image"
        bootable = True
    elif snapshot_ref:
        snap = require_snapshot(db, snapshot_ref)
        snapshot_id = snap.id
        source_type = "snapshot"
        backend = snap.backend or backend
        source = db.get(Volume, snap.volume_id)
        host = source.host if source else None
    elif source_vol_ref:
        src = require_volume(db, source_vol_ref)
        source_volume_id = src.id
        source_type = "volume"
        bootable = src.bootable
        backend = src.backend or backend
        host = src.host
    else:
        source_type = source_type or "blank"

    if backend == "lvm" and not (snapshot_ref or source_vol_ref):
        host = pick_lvm_host(db)

    # Create DB record immediately with status='creating'
    volume = Volume(
        id=volume_id,
        name=payload.name,
        size_gb=payload.size_gb,
        status="creating",
        path="",                          # worker fills this in
        backend=backend,
        host=host,
        bootable=bootable,
        image_id=image_ref or None,
        snapshot_id=snapshot_id,
        source_volume_id=source_volume_id,
        source_type=source_type,
        volume_type=vt.name,
        project_id=project_id,
        multiattach=multiattach,
    )
    db.add(volume)
    db.commit()
    db.refresh(volume)

    # Cast to worker for actual storage creation
    clients.cast_to_worker("create_volume", {
        "volume_id":       volume_id,
        "name":            payload.name,
        "size_gb":         payload.size_gb,
        "backend":         backend,
        "image_ref":       image_ref,
        "snapshot_id":     snapshot_id,
        "source_volume_id": source_volume_id,
        "bootable":        bootable,
        "lvm_type":        getattr(vt, "lvm_type", "default"),
        "max_iops":        getattr(vt, "max_iops", None),       # Ceph QoS: IOPS limit
        "max_throughput":  getattr(vt, "max_throughput", None), # Ceph QoS: MB/s limit
        "_image_api_url":  IMAGE_API_URL,
    })
    try:
        clients._rpc.publish_event("volume.create.start", {
            "volume_id":  volume_id,
            "name":       payload.name,
            "size_gb":    payload.size_gb,
            "project_id": project_id,
            "status":     "creating",
        })
    except Exception as exc:
        log.warning("volume.create.start event not published: %s", exc)

    return obj_to_dict(volume)


def _list_volumes_query(current: CurrentUser, db: DB):
    q = select(Volume)
    if current.get("role") != "admin":
        q = q.where(Volume.project_id == current["project_id"])
    return q


def _reconcile_orphan_volumes(db: DB):
    """Mark volumes as available if their attached server no longer exists."""
    orphan = (
        select(Volume)
        .where(Volume.status == "in-use")
        .where(Volume.attached_to_server_id.isnot(None))
    )
    try:
        resp = requests.get(f"{COMPUTE_API_URL}/servers", timeout=5)
        if resp.status_code == 200:
            servers = resp.json().get("items", [])
            active_ids = {s.get("id") for s in servers}
            for vol in db.scalars(orphan).all():
                if vol.attached_to_server_id not in active_ids:
                    vol.attached_to_server_id = None
                    vol.target_device = None
                    vol.status = "available"
        db.commit()
    except Exception as exc:
        log.warning("orphan volume reconcile failed: %s", exc)
        db.rollback()


def _do_create_snapshot(db: Session, volume_ref: str, snap_name: str) -> dict:
    if get_snapshot(db, snap_name):
        raise HTTPException(status_code=409, detail="snapshot already exists")
    source = require_volume(db, volume_ref)

    snapshot_id = new_id()
    import math
    cow_gb = max(1, math.ceil(source.size_gb * 0.2))

    # Create record with status='creating'
    snap = Snapshot(
        id=snapshot_id,
        name=snap_name,
        volume_id=source.id,
        size_gb=source.size_gb,
        status="creating",
        path="",
        backend=source.backend,
    )
    db.add(snap)
    db.commit()
    db.refresh(snap)

    clients.cast_to_worker("create_snapshot", {
        "snapshot_id":  snapshot_id,
        "name":         snap_name,
        "volume_id":    source.id,
        "volume_path":  source.path,
        "backend":      source.backend,
        "cow_gb":       cow_gb,
    })

    return obj_to_dict(snap)
