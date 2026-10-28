import requests
from fastapi import APIRouter, HTTPException
from shared.auth_middleware import AdminUser, CurrentUser, InternalAuth, UserOrInternal
from shared.catalog import CatalogError
from shared.internal import internal_headers
from sqlalchemy import select

from .. import clients
from ..clients import _response_detail, get_server
from ..config import compute_api_url
from ..db import DB
from ..lookups import next_target_device, require_volume
from ..models import Snapshot, Volume, VolumeQuota
from ..schemas import AttachIn, InternalAttachIn, VolumeIn, VolumeInternalIn
from ..serializers import obj_to_dict, volume_to_os_dict
from ..service import (
    _check_auth_volume_quota,
    _create_volume_record,
    _list_volumes_query,
    _reconcile_orphan_volumes,
)

router = APIRouter()


# ── Volume CRUD ────────────────────────────────────────────────────────────────

@router.post("/volumes", status_code=201)
def create_volume(payload: VolumeIn, current: UserOrInternal, db: DB):
    size = payload.size_gb or payload.size
    if not size:
        raise HTTPException(status_code=400, detail="size_gb is required")
    project_id = current["project_id"]
    # ── Quota validation ───────────────────────────────────────────
    _check_auth_volume_quota(project_id, size, db)
    quota = db.get(VolumeQuota, project_id)
    if quota:
        current_vols = db.scalars(
            select(Volume).where(
                Volume.project_id == project_id,
                Volume.status.not_in(["deleting", "error"]),
            )
        ).all()
        if len(current_vols) >= quota.volumes:
            raise HTTPException(
                status_code=413,
                detail=f"Volume quota exceeded: {len(current_vols)}/{quota.volumes} volumes used",
            )
        used_gb = sum(v.size_gb for v in current_vols)
        if used_gb + size > quota.gigabytes:
            raise HTTPException(
                status_code=413,
                detail=f"Volume GB quota exceeded: {used_gb + size}/{quota.gigabytes} GB would be used",
            )
    internal = VolumeInternalIn(
        name=payload.name,
        size_gb=size,
        image_ref=payload.image or payload.image_id,
        snapshot_id=payload.snapshot,
        source_volume_id=payload.source_volume,
        bootable=payload.bootable,
        volume_type=payload.volume_type,
    )
    return _create_volume_record(db, internal, project_id=project_id, multiattach=payload.multiattach)


@router.post("/internal/volumes", status_code=201, dependencies=[InternalAuth])
def create_volume_internal(payload: VolumeInternalIn, db: DB):
    return _create_volume_record(db, payload, project_id=payload.project_id)


@router.post("/internal/fence", dependencies=[InternalAuth])
def fence(payload: dict, db: DB):
    """Revoke a dead compute node's iSCSI access to the given volumes. 200 even when some fail; the caller must read results."""
    results = {}
    for vid in payload.get("volume_ids", []):
        try:
            clients.call_worker(require_volume(db, vid).host, "unexport_volume", {"volume_id": vid, "initiator_iqn": payload["initiator_iqn"]})
            results[vid] = "ok"
        except Exception as exc:  # best-effort: per-volume failure is reported to the caller, not raised
            results[vid] = f"error: {exc}"[:200]
    return {"results": results}

@router.get("/internal/volumes/{ref}", dependencies=[InternalAuth])
def get_volume_internal(ref: str, db: DB):
    """No-auth endpoint for internal services (e.g. compute-worker) to poll volume status."""
    volume = require_volume(db, ref)
    return {
        "id":       volume.id,
        "name":     volume.name,
        "size_gb":  volume.size_gb,
        "status":   volume.status,
        "path":     volume.path,
        "bootable": volume.bootable,
        "backend":  volume.backend,
        "host":     volume.host,
        "target_device": volume.target_device,
        "error_message": volume.error_message,
    }


@router.delete("/internal/volumes/{ref}", status_code=202, dependencies=[InternalAuth])
def delete_volume_internal(ref: str, db: DB):
    """No-auth endpoint for internal services (e.g. compute-worker) to delete root volumes."""
    volume = require_volume(db, ref)
    if volume.status == "in-use":
        raise HTTPException(status_code=409, detail="volume is in-use, detach first")
    volume.status = "deleting"
    db.commit()
    clients.cast_to_worker("delete_volume", {
        "volume_id": volume.id,
        "name":      volume.name,
        "path":      volume.path,
        "backend":   volume.backend,
    })
    return {"status": "deleting", "id": volume.id}


@router.get("/volumes")
def list_volumes(current: UserOrInternal, db: DB):
    _reconcile_orphan_volumes(db)
    q = _list_volumes_query(current, db)
    return {"items": [volume_to_os_dict(v) for v in db.scalars(q.order_by(Volume.created_at)).all()]}


@router.get("/volumes/{ref}")
def show_volume(ref: str, current: UserOrInternal, db: DB):
    return volume_to_os_dict(require_volume(db, ref, current))


@router.delete("/volumes/{ref}")
def delete_volume(ref: str, current: UserOrInternal, db: DB):
    volume = require_volume(db, ref, current)
    if volume.status == "in-use":
        raise HTTPException(status_code=409, detail="detach volume before delete")
    if db.scalar(select(Snapshot.id).where(Snapshot.volume_id == volume.id).limit(1)):
        # the backend cannot remove a volume that has snapshots, and the row would be gone, leaving the backend volume behind
        raise HTTPException(status_code=409, detail="volume has snapshots, delete them first")
    volume.status = "deleting"
    db.commit()
    # Cast to worker for backend cleanup, then conductor will delete the record
    clients.cast_to_worker("delete_volume", {
        "volume_id": volume.id,
        "name":      volume.name,
        "path":      volume.path,
        "backend":   volume.backend,
    })
    return {"status": "deleting", "id": volume.id, "name": volume.name}


@router.post("/volumes/{ref}/extend")
def extend_volume(ref: str, payload: dict, current: CurrentUser, db: DB):
    volume = require_volume(db, ref, current)
    if volume.status == "in-use":
        raise HTTPException(status_code=409, detail="cannot extend an attached volume")
    new_size = payload.get("new_size") or payload.get("new_size_gb")
    if not isinstance(new_size, int) or isinstance(new_size, bool) or new_size <= volume.size_gb:
        raise HTTPException(status_code=400, detail=f"new_size must be > current size ({volume.size_gb} GB)")
    volume.status = "extending"
    db.commit()
    clients.cast_to_worker("extend_volume", {
        "volume_id": volume.id,
        "name":      volume.name,
        "path":      volume.path,
        "backend":   volume.backend,
        "new_size_gb": new_size,
    })
    db.refresh(volume)
    return obj_to_dict(volume)


# ── Internal volume attachment (called by compute-worker) ──────────────────────

@router.post("/internal/volumes/{ref}/attach-server", dependencies=[InternalAuth])
def internal_attach_server(ref: str, payload: InternalAttachIn, db: DB):
    """Called by compute-worker after spawning a VM to mark root volume as in-use."""
    volume = require_volume(db, ref)
    # Multi-attach: allow if volume.multiattach is True, otherwise enforce single-attach
    if volume.status == "in-use" and volume.attached_to_server_id != payload.server_id:
        if not volume.multiattach:
            raise HTTPException(status_code=409, detail="volume is already attached (set multiattach=true to allow multiple attachments)")
        # Multi-attach: update the primary attachment record (first attachment wins for tracking)
    volume.attached_to_server_id = payload.server_id
    volume.target_device = payload.target_device or volume.target_device or (
        "sda" if volume.bootable else next_target_device(db, payload.server_id)
    )
    volume.status = "in-use"
    db.commit()
    db.refresh(volume)
    return obj_to_dict(volume)


@router.post("/internal/volumes/{ref}/detach-server", dependencies=[InternalAuth])
def internal_detach_server(ref: str, db: DB):
    """Called by compute-worker to release a volume after VM destruction."""
    volume = require_volume(db, ref)
    volume.attached_to_server_id = None
    volume.target_device = None
    volume.status = "available"
    db.commit()
    db.refresh(volume)
    return obj_to_dict(volume)


@router.post("/servers/{server_id}/volumes/detach-all", dependencies=[InternalAuth])
def detach_server_volumes(server_id: str, db: DB):
    """Release all volumes attached to a server (called during VM delete)."""
    volumes = db.scalars(select(Volume).where(Volume.attached_to_server_id == server_id)).all()
    detached = []
    for volume in volumes:
        volume.attached_to_server_id = None
        volume.target_device = None
        volume.status = "available"
        detached.append(obj_to_dict(volume))
    db.commit()
    return {"server_id": server_id, "detached": detached}


@router.post("/volumes/{ref}/force-detach")
def force_detach_volume(ref: str, _: AdminUser, db: DB):
    volume = require_volume(db, ref)
    volume.attached_to_server_id = None
    volume.target_device = None
    volume.status = "available"
    db.commit()
    db.refresh(volume)
    return obj_to_dict(volume)


@router.post("/volumes/{ref}/attach")
def attach_volume(ref: str, payload: AttachIn, current: CurrentUser, db: DB):
    """Hotplug attach via compute-api → compute-worker (hotplug path)."""
    volume = require_volume(db, ref, current)
    if volume.status == "in-use":
        raise HTTPException(status_code=409, detail="volume is already attached")
    server = get_server(payload.server)
    if server.get("status") not in {"ACTIVE", "SHUTOFF"}:
        raise HTTPException(
            status_code=409,
            detail=f"VM must be ACTIVE or SHUTOFF before attach; current status is {server.get('status')}",
        )
    target_device = next_target_device(db, server["id"])
    # Call compute-api which casts to compute-worker
    try:
        resp = requests.post(
            f"{compute_api_url()}/servers/{server['id']}/volumes/attach",
            json={
                "volume_id":   volume.id,
                "volume_name": volume.name,
                "path":        volume.path,
                "backend":     volume.backend or "lvm",
                "host":        volume.host,
                "target_dev":  target_device,
            },
            headers=internal_headers(),
            timeout=60,
        )
        if resp.status_code >= 400:
            raise HTTPException(status_code=502, detail=f"compute-api attach failed: {_response_detail(resp)}")
    except (requests.RequestException, CatalogError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    volume.attached_to_server_id = server["id"]
    volume.target_device = target_device
    volume.status = "in-use"
    db.commit()
    db.refresh(volume)
    return obj_to_dict(volume)


@router.post("/volumes/{ref}/detach")
def detach_volume(ref: str, current: CurrentUser, db: DB):
    """Hotplug detach via compute-api → compute-worker (hotplug path).
    If compute-api returns 404 (the instance was already deleted), still
    release the attachment locally so the volume does not get stuck 'in-use'.
    """
    volume = require_volume(db, ref, current)
    if volume.status != "in-use" or not volume.attached_to_server_id:
        raise HTTPException(status_code=409, detail="volume is not attached")
    try:
        resp = requests.post(
            f"{compute_api_url()}/servers/{volume.attached_to_server_id}/volumes/detach",
            json={
                "volume_id":   volume.id,
                "volume_name": volume.name,
                "path":        volume.path,
                "backend":     volume.backend or "lvm",
                "host":        volume.host,
                "target_dev":  volume.target_device,
            },
            headers=internal_headers(),
            timeout=60,
        )
        if resp.status_code == 404:
            # the instance was already deleted — still release the attachment locally
            volume.attached_to_server_id = None
            volume.target_device = None
            volume.status = "available"
            db.commit()
            db.refresh(volume)
            return obj_to_dict(volume)
        if resp.status_code >= 400:
            raise HTTPException(status_code=502, detail=f"compute-api detach failed: {_response_detail(resp)}")
    except (requests.RequestException, CatalogError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    volume.attached_to_server_id = None
    volume.target_device = None
    volume.status = "available"
    db.commit()
    db.refresh(volume)
    return obj_to_dict(volume)
