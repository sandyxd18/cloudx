import time
import uuid
from datetime import datetime

from .config import log
from .db import SessionLocal
from .models import Snapshot, Volume, VolumeSnapshotSchedule

# ── Startup ────────────────────────────────────────────────────────────────────

def _run_snapshot_schedules():
    db = SessionLocal()
    try:
        schedules = db.query(VolumeSnapshotSchedule).all()
        for sch in schedules:
            now_dt = datetime.utcnow()
            if not sch.last_run_at or (now_dt - sch.last_run_at).total_seconds() >= sch.interval_seconds:
                vol = db.get(Volume, sch.volume_id)
                if not vol or vol.status in ("deleting", "error", "deleted"):
                    continue
                snap_name = f"auto-snap-{vol.name[:10]}-{uuid.uuid4().hex[:6]}"
                snap = Snapshot(volume_id=vol.id, name=snap_name, size_gb=vol.size_gb, status="available")
                db.add(snap)
                sch.last_run_at = now_dt
                db.commit()

                snaps = db.query(Snapshot).filter_by(volume_id=vol.id).order_by(Snapshot.created_at.desc()).all()
                if len(snaps) > sch.retention_count:
                    for old_snap in snaps[sch.retention_count:]:
                        db.delete(old_snap)
                    db.commit()
    except Exception as exc:
        log.warning("Error running snapshot schedules: %s", exc)
    finally:
        db.close()


def _snapshot_schedule_loop():
    while True:
        time.sleep(10)
        _run_snapshot_schedules()
