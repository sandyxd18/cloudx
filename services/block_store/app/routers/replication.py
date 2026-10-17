from fastapi import APIRouter, HTTPException
from shared.auth_middleware import CurrentUser

from .. import clients
from ..db import DB
from ..lookups import require_volume
from ..schemas import ReplicationEnableIn
from ..serializers import obj_to_dict

router = APIRouter()


@router.post("/volumes/{ref}/enable-replication")
def enable_volume_replication(ref: str, payload: ReplicationEnableIn, current: CurrentUser, db: DB):
    volume = require_volume(db, ref, current)
    target = payload.target
    if not target:
        raise HTTPException(status_code=400, detail="replication target is required")
    if volume.host and target == volume.host:
        raise HTTPException(status_code=400, detail="replication target cannot be the same as volume host")

    volume.replication_status = "enabled"
    volume.replication_target = target
    volume.replication_mode = payload.mode
    db.commit()
    db.refresh(volume)

    clients.cast_to_worker("replica_create", {
        "volume_id": volume.id,
        "size_gb": volume.size_gb,
        "sync_from": volume.host,
        "target_host": target,
        "host": target,
    })
    return obj_to_dict(volume)


@router.post("/volumes/{ref}/sync-replication")
def sync_volume_replication(ref: str, current: CurrentUser, db: DB):
    volume = require_volume(db, ref, current)
    if volume.replication_status not in ("enabled", "synced", "replicating"):
        raise HTTPException(status_code=400, detail="Replication is not enabled for this volume")
    
    volume.replication_status = "replicating"
    db.commit()

    clients.cast_to_worker("replica_sync", {
        "volume_id": volume.id,
        "size_gb": volume.size_gb,
        "target_host": volume.replication_target,
        "host": volume.host,
    })
    db.refresh(volume)
    return {
        "status": "replicating",
        "last_replicated_at": volume.last_replicated_at.isoformat() if volume.last_replicated_at else None,
        "volume_id": volume.id,
    }


@router.post("/volumes/{ref}/failover")
def failover_volume_replication(ref: str, current: CurrentUser, db: DB, force: bool = False):
    volume = require_volume(db, ref, current)
    if volume.status == "in-use" and not force:
        raise HTTPException(status_code=409, detail="cannot failover an attached volume without force=true")

    if volume.replication_status != "synced":
        raise HTTPException(status_code=409, detail=f"replication status must be synced to failover, current is {volume.replication_status}")

    target = volume.replication_target
    if not target:
        raise HTTPException(status_code=400, detail="replication target not configured")

    clients.cast_to_worker("replica_promote", {
        "volume_id": volume.id,
        "host": target,
    })
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
def disable_volume_replication(ref: str, current: CurrentUser, db: DB):
    volume = require_volume(db, ref, current)
    target = volume.replication_target
    if target:
        clients.cast_to_worker("replica_delete", {
            "volume_id": volume.id,
            "host": target,
        })
    volume.replication_status = "disabled"
    volume.replication_target = None
    db.commit()
    db.refresh(volume)
    return {
        "status": "disabled",
        "replication_status": "disabled",
        "id": volume.id,
    }
