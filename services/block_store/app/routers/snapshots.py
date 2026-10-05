from fastapi import APIRouter
from shared.auth_middleware import CurrentUser
from sqlalchemy import select

from .. import clients
from ..db import DB
from ..lookups import require_snapshot
from ..models import Snapshot
from ..schemas import SnapshotIn
from ..serializers import snapshot_to_os_dict
from ..service import _do_create_snapshot

router = APIRouter()


# ── Snapshots ──────────────────────────────────────────────────────────────────

@router.post("/volumes/{ref}/snapshots", status_code=201)
def create_volume_snapshot(ref: str, payload: SnapshotIn, _: CurrentUser, db: DB):
    return _do_create_snapshot(db, ref, payload.name)


@router.post("/snapshots", status_code=201)
def create_snapshot(payload: SnapshotIn, volume: str, db: DB):
    return _do_create_snapshot(db, volume, payload.name)


@router.get("/snapshots")
def list_snapshots(_: CurrentUser, db: DB):
    return {"items": [snapshot_to_os_dict(s) for s in db.scalars(select(Snapshot).order_by(Snapshot.created_at)).all()]}


@router.get("/snapshots/{ref}")
def show_snapshot(ref: str, _: CurrentUser, db: DB):
    return snapshot_to_os_dict(require_snapshot(db, ref))


@router.delete("/snapshots/{ref}")
def delete_snapshot(ref: str, _: CurrentUser, db: DB):
    snapshot = require_snapshot(db, ref)
    snapshot.status = "deleting"
    db.commit()
    clients.cast_to_worker("delete_snapshot", {
        "snapshot_id": snapshot.id,
        "name":        snapshot.name,
        "path":        snapshot.path,
        "backend":     snapshot.backend,
    })
    return {"status": "deleting", "id": snapshot.id, "name": snapshot.name}
