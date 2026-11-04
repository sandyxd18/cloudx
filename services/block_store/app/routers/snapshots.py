from fastapi import APIRouter
from shared.auth_middleware import CurrentUser, UserOrInternal
from sqlalchemy import select

from .. import clients
from ..db import DB
from ..lookups import can_access, require_snapshot, require_volume
from ..models import Snapshot, Volume
from ..schemas import ImageUploadIn, SnapshotIn
from ..serializers import snapshot_to_os_dict
from ..service import _do_create_snapshot

router = APIRouter()


# ── Snapshots ──────────────────────────────────────────────────────────────────

@router.post("/volumes/{ref}/snapshots", status_code=201)
def create_volume_snapshot(ref: str, payload: SnapshotIn, current: UserOrInternal, db: DB):
    require_volume(db, ref, current)
    return _do_create_snapshot(db, ref, payload.name)


@router.post("/volumes/{ref}/upload-image", status_code=202)
def upload_volume_image(ref: str, payload: ImageUploadIn, current: UserOrInternal, db: DB):
    """Snapshot the volume and upload the snapshot as an image (Cinder's os-volume_upload_image). The work is done by
    the block-store-worker that owns the volume; the image appears when it ends, and a failure leaves the snapshot in
    `error` with the reason. Returns the snapshot."""
    volume = require_volume(db, ref, current)
    owner = payload.project_id if current.get("role") == "admin" else None  # only an admin or a service names the owner
    image = {"name": payload.image_name, "project_id": owner or volume.project_id,
             "visibility": payload.visibility, "disk_format": "qcow2"}
    return _do_create_snapshot(db, ref, f"{payload.image_name}-root", upload_image=image)


@router.post("/snapshots", status_code=201)
def create_snapshot(payload: SnapshotIn, volume: str, current: CurrentUser, db: DB):
    require_volume(db, volume, current)
    return _do_create_snapshot(db, volume, payload.name)


@router.get("/snapshots")
def list_snapshots(current: CurrentUser, db: DB):
    owner = {v.id: v.project_id for v in db.scalars(select(Volume)).all()}
    return {"items": [snapshot_to_os_dict(s) for s in db.scalars(select(Snapshot).order_by(Snapshot.created_at)).all()
                      if can_access(owner.get(s.volume_id), current)]}


@router.get("/snapshots/{ref}")
def show_snapshot(ref: str, current: CurrentUser, db: DB):
    return snapshot_to_os_dict(require_snapshot(db, ref, current))


@router.delete("/snapshots/{ref}")
def delete_snapshot(ref: str, current: CurrentUser, db: DB):
    snapshot = require_snapshot(db, ref, current)
    snapshot.status = "deleting"
    db.commit()
    clients.cast_to_worker("delete_snapshot", {
        "snapshot_id": snapshot.id,
        "name":        snapshot.name,
        "path":        snapshot.path,
        "backend":     snapshot.backend,
    })
    return {"status": "deleting", "id": snapshot.id, "name": snapshot.name}
