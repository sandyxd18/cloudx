from datetime import datetime

from fastapi import APIRouter, HTTPException
from shared.auth_middleware import CurrentUser

from ..db import DB
from ..lookups import require_volume
from ..schemas import ReplicationEnableIn
from ..serializers import obj_to_dict

router = APIRouter()


@router.post("/volumes/{ref}/enable-replication")
def enable_volume_replication(ref: str, payload: ReplicationEnableIn, _: CurrentUser, db: DB):
    volume = require_volume(db, ref)
    volume.replication_status = "enabled"
    volume.replication_target = payload.target
    volume.replication_mode = payload.mode
    db.commit()
    db.refresh(volume)
    return obj_to_dict(volume)


@router.post("/volumes/{ref}/sync-replication")
def sync_volume_replication(ref: str, _: CurrentUser, db: DB):
    volume = require_volume(db, ref)
    if volume.replication_status not in ("enabled", "synced", "replicating"):
        raise HTTPException(status_code=400, detail="Replication is not enabled for this volume")
    volume.last_replicated_at = datetime.utcnow()
    volume.replication_status = "synced"
    db.commit()
    db.refresh(volume)
    return {
        "status": "synced",
        "last_replicated_at": volume.last_replicated_at.isoformat() if volume.last_replicated_at else None,
        "volume_id": volume.id,
    }


@router.post("/volumes/{ref}/failover")
def failover_volume_replication(ref: str, _: CurrentUser, db: DB):
    volume = require_volume(db, ref)
    volume.replication_status = "failed-over"
    db.commit()
    db.refresh(volume)
    return {
        "status": "failed-over",
        "id": volume.id,
        "replication_status": volume.replication_status,
        "replication_target": volume.replication_target,
    }


@router.post("/volumes/{ref}/disable-replication")
def disable_volume_replication(ref: str, _: CurrentUser, db: DB):
    volume = require_volume(db, ref)
    volume.replication_status = "disabled"
    volume.replication_target = None
    db.commit()
    db.refresh(volume)
    return {
        "status": "disabled",
        "replication_status": "disabled",
        "id": volume.id,
    }
