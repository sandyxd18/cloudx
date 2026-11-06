from fastapi import APIRouter, HTTPException
from shared.auth_middleware import CurrentUser

from ..db import DB
from ..lookups import can_access, require_volume
from ..models import Volume, VolumeSnapshotSchedule
from ..schemas import SnapshotScheduleIn

router = APIRouter()


# ── Snapshot Schedules ─────────────────────────────────────────────────────────

@router.post("/volumes/{ref}/schedules", status_code=201)
def create_volume_schedule(ref: str, payload: SnapshotScheduleIn, current: CurrentUser, db: DB):
    vol = require_volume(db, ref, current)
    sch = db.query(VolumeSnapshotSchedule).filter_by(volume_id=vol.id).first()
    if not sch:
        sch = VolumeSnapshotSchedule(
            volume_id=vol.id,
            interval_seconds=payload.interval_seconds,
            retention_count=payload.retention_count,
        )
        db.add(sch)
    else:
        sch.interval_seconds = payload.interval_seconds
        sch.retention_count = payload.retention_count
    db.commit()
    db.refresh(sch)
    return {
        "id": sch.id,
        "volume_id": sch.volume_id,
        "interval_seconds": sch.interval_seconds,
        "retention_count": sch.retention_count,
        "last_run_at": sch.last_run_at.isoformat() if sch.last_run_at else None,
    }


@router.get("/volumes/{ref}/schedules")
def get_volume_schedule(ref: str, current: CurrentUser, db: DB):
    vol = require_volume(db, ref, current)
    sch = db.query(VolumeSnapshotSchedule).filter_by(volume_id=vol.id).first()
    if not sch:
        raise HTTPException(status_code=404, detail="schedule not found")
    return {
        "id": sch.id,
        "volume_id": sch.volume_id,
        "interval_seconds": sch.interval_seconds,
        "retention_count": sch.retention_count,
        "last_run_at": sch.last_run_at.isoformat() if sch.last_run_at else None,
    }


@router.delete("/schedules/{sch_id}", status_code=204)
def delete_volume_schedule(sch_id: str, current: CurrentUser, db: DB):
    sch = db.get(VolumeSnapshotSchedule, sch_id)
    if not sch:
        return
    vol = db.get(Volume, sch.volume_id)
    # a schedule follows its volume's project; one whose volume is gone is left to an admin
    allowed = can_access(vol.project_id, current) if vol else current.get("role") == "admin"
    if not allowed:
        raise HTTPException(status_code=404, detail="schedule not found")
    db.delete(sch)
    db.commit()
