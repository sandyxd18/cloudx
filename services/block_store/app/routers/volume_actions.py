from fastapi import APIRouter, HTTPException
from shared.auth_middleware import CurrentUser

from .. import clients
from ..config import log
from ..db import DB
from ..lookups import require_volume
from ..serializers import obj_to_dict

router = APIRouter()


@router.post("/volumes/{ref}/action")
def volume_action(ref: str, payload: dict, current: CurrentUser, db: DB):
    """
    Perform an action on a volume. Supported actions:

    - os-migrate_volume: Migrate a volume to a different backend/host.
      Payload: {"os-migrate_volume": {"host": "...", "force_host_copy": false}}

    - os-force_delete: Admin force-delete a volume regardless of state.
      Payload: {"os-force_delete": {}}

    - os-reset_status: Reset volume status (admin only).
      Payload: {"os-reset_status": {"status": "available"}}

    - os-extend_volume: Extend volume size.
      Payload: {"os-extend_volume": {"new_size": 20}}
    """
    volume = require_volume(db, ref)

    # ── os-migrate_volume ──────────────────────────────────────────────────
    if "os-migrate_volume" in payload:
        data = payload["os-migrate_volume"]
        target_host = data.get("host")
        if not target_host:
            raise HTTPException(status_code=400, detail="'host' is required in os-migrate_volume")
        # Validate state — only available volumes can be migrated without live_migration flag
        if volume.status not in ("available",):
            raise HTTPException(
                status_code=409,
                detail=f"Volume must be in 'available' state to migrate (current: {volume.status})"
            )
        volume.status = "migrating"
        db.commit()
        clients.cast_to_worker("migrate_volume", {
            "volume_id":        volume.id,
            "name":             volume.name,
            "path":             volume.path,
            "backend":          volume.backend,
            "size_gb":          volume.size_gb,
            "target_host":      target_host,
            "force_host_copy":  data.get("force_host_copy", False),
            "lock_volume":      data.get("lock_volume", False),
        })
        log.info("Volume migration dispatched: %s → %s", volume.id, target_host)
        return {"migration_status": "migrating", "volume_id": volume.id, "target_host": target_host}

    # ── os-force_delete ────────────────────────────────────────────────────
    elif "os-force_delete" in payload:
        if current.get("role") != "admin":
            raise HTTPException(status_code=403, detail="admin role required for force_delete")
        volume.status = "deleting"
        db.commit()
        clients.cast_to_worker("delete_volume", {
            "volume_id": volume.id,
            "name":      volume.name,
            "path":      volume.path,
            "backend":   volume.backend,
        })
        return {"status": "deleting", "id": volume.id}

    # ── os-reset_status ────────────────────────────────────────────────────
    elif "os-reset_status" in payload:
        if current.get("role") != "admin":
            raise HTTPException(status_code=403, detail="admin role required for reset_status")
        new_status = payload["os-reset_status"].get("status", "available")
        valid_statuses = {"available", "in-use", "error", "creating", "deleting", "extending"}
        if new_status not in valid_statuses:
            raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {valid_statuses}")
        volume.status = new_status
        db.commit()
        return obj_to_dict(volume)

    # ── os-extend_volume ───────────────────────────────────────────────────
    elif "os-extend_volume" in payload:
        new_size = payload["os-extend_volume"].get("new_size")
        if not new_size or new_size <= volume.size_gb:
            raise HTTPException(status_code=400, detail=f"new_size must be > current size ({volume.size_gb} GB)")
        if volume.status == "in-use":
            raise HTTPException(status_code=409, detail="cannot extend an attached volume")
        volume.status = "extending"
        db.commit()
        clients.cast_to_worker("extend_volume", {
            "volume_id":   volume.id,
            "name":        volume.name,
            "path":        volume.path,
            "backend":     volume.backend,
            "new_size_gb": new_size,
        })
        db.refresh(volume)
        return obj_to_dict(volume)

    else:
        raise HTTPException(status_code=400, detail=f"Unknown action: {list(payload.keys())}")
