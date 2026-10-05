import json

from fastapi import APIRouter, HTTPException
from shared.auth_middleware import CurrentUser
from sqlalchemy import select

from .. import clients
from ..config import log
from ..db import DB
from ..lookups import require_cg
from ..models import CGSnapshot, ConsistencyGroup, Volume
from ..schemas import CGSnapshotIn, ConsistencyGroupIn
from ..serializers import _fmt_ts

router = APIRouter()


# ── Consistency Groups ────────────────────────────────────────────────────────
# A consistency group is a set of volumes whose snapshots are captured
# atomically (or as close to atomically as the backend allows).
# Reference: Block storage consistency groups specification

def _cg_to_dict(cg: ConsistencyGroup) -> dict:
    return {
        "id":          cg.id,
        "name":        cg.name,
        "description": cg.description,
        "status":      cg.status,
        "project_id":  cg.project_id,
        "volume_ids":  json.loads(cg.volume_ids or "[]"),
        "created_at":  _fmt_ts(cg.created_at),
    }


def _cgs_to_dict(cgs: CGSnapshot) -> dict:
    return {
        "id":                   cgs.id,
        "name":                 cgs.name,
        "description":          cgs.description,
        "consistency_group_id": cgs.consistency_group_id,
        "status":               cgs.status,
        "project_id":           cgs.project_id,
        "created_at":           _fmt_ts(cgs.created_at),
    }


@router.post("/consistencygroups", status_code=201)
def create_consistency_group(payload: ConsistencyGroupIn, current: CurrentUser, db: DB):
    """Create a new consistency group optionally pre-populated with volumes."""
    # Validate that all provided volume_ids exist
    for vid in payload.volume_ids:
        vol = db.scalar(select(Volume).where((Volume.id == vid) | (Volume.name == vid)))
        if not vol:
            raise HTTPException(status_code=404, detail=f"Volume '{vid}' not found")

    cg = ConsistencyGroup(
        name=payload.name,
        description=payload.description,
        status="available",
        project_id=current.get("project_id"),
        volume_ids=json.dumps(payload.volume_ids),
    )
    db.add(cg)
    db.commit()
    db.refresh(cg)
    log.info("ConsistencyGroup %s created with %d volumes", cg.id, len(payload.volume_ids))
    return _cg_to_dict(cg)


@router.get("/consistencygroups")
def list_consistency_groups(current: CurrentUser, db: DB):
    """List all consistency groups for the current project."""
    project_id = current.get("project_id")
    q = select(ConsistencyGroup)
    if current.get("role") != "admin":
        q = q.where(ConsistencyGroup.project_id == project_id)
    cgs = db.scalars(q.order_by(ConsistencyGroup.created_at.desc())).all()
    return {"consistencygroups": [_cg_to_dict(cg) for cg in cgs]}


@router.get("/consistencygroups/{ref}")
def get_consistency_group(ref: str, _: CurrentUser, db: DB):
    return _cg_to_dict(require_cg(db, ref))


@router.post("/consistencygroups/{ref}/action")
def consistency_group_action(ref: str, payload: dict, _: CurrentUser, db: DB):
    """
    Perform an action on a consistency group.

    Supported actions:
    - add_volumes: {"add_volumes": {"volumes": ["vol_id_1", "vol_id_2"]}}
    - remove_volumes: {"remove_volumes": {"volumes": ["vol_id_1"]}}
    - delete: {"delete": {}}
    """
    cg = require_cg(db, ref)

    if "add_volumes" in payload:
        current_ids = set(json.loads(cg.volume_ids or "[]"))
        new_ids = payload["add_volumes"].get("volumes", [])
        for vid in new_ids:
            vol = db.scalar(select(Volume).where((Volume.id == vid) | (Volume.name == vid)))
            if not vol:
                raise HTTPException(status_code=404, detail=f"Volume '{vid}' not found")
            current_ids.add(vol.id)
        cg.volume_ids = json.dumps(list(current_ids))
        db.commit()
        db.refresh(cg)
        return _cg_to_dict(cg)

    elif "remove_volumes" in payload:
        current_ids = set(json.loads(cg.volume_ids or "[]"))
        remove_ids = set(payload["remove_volumes"].get("volumes", []))
        cg.volume_ids = json.dumps(list(current_ids - remove_ids))
        db.commit()
        db.refresh(cg)
        return _cg_to_dict(cg)

    elif "delete" in payload:
        db.delete(cg)
        db.commit()
        return {"status": "deleted", "id": cg.id}

    else:
        raise HTTPException(status_code=400, detail=f"Unknown action: {list(payload.keys())}")


@router.delete("/consistencygroups/{ref}", status_code=202)
def delete_consistency_group(ref: str, _: CurrentUser, db: DB):
    """Delete a consistency group (does NOT delete the volumes inside it)."""
    cg = require_cg(db, ref)
    db.delete(cg)
    db.commit()
    return {"status": "deleted", "id": cg.id}


# ── CG Snapshots ──────────────────────────────────────────────────────────────

@router.post("/cgsnapshots", status_code=201)
def create_cgsnapshot(payload: CGSnapshotIn, current: CurrentUser, db: DB):
    """
    Create an atomic snapshot of all volumes in a consistency group.

    For Ceph backends: uses rbd group snapshot (atomic).
    For LVM backends: snapshots volumes sequentially (best-effort).
    """
    cg = require_cg(db, payload.consistency_group_id)
    volume_ids = json.loads(cg.volume_ids or "[]")
    if not volume_ids:
        raise HTTPException(status_code=400, detail="Consistency group has no volumes")
    hosts = {v.host for v in db.scalars(select(Volume).where(Volume.id.in_(volume_ids)))}
    if len(hosts) > 1:
        raise HTTPException(status_code=409, detail="consistency group volumes live on different storage nodes")

    cgs = CGSnapshot(
        name=payload.name,
        description=payload.description,
        consistency_group_id=cg.id,
        status="creating",
        project_id=current.get("project_id"),
    )
    db.add(cgs)
    db.commit()
    db.refresh(cgs)

    # Gather volume info for each member volume
    volumes_info = []
    for vid in volume_ids:
        vol = db.scalar(select(Volume).where(Volume.id == vid))
        if vol:
            volumes_info.append({
                "volume_id": vol.id,
                "name":      vol.name,
                "path":      vol.path,
                "backend":   vol.backend,
                "size_gb":   vol.size_gb,
            })

    # Dispatch async to worker — sequential loop snapshot
    clients.cast_to_worker("create_cgsnapshot", {
        "cgsnapshot_id":      cgs.id,
        "consistency_group_id": cg.id,
        "volumes":            volumes_info,
        "host":               next(iter(hosts), None),
    })
    log.info("CGSnapshot %s dispatched for group %s (%d volumes)", cgs.id, cg.id, len(volumes_info))
    return _cgs_to_dict(cgs)


@router.get("/cgsnapshots")
def list_cgsnapshots(current: CurrentUser, db: DB):
    """List all CG snapshots for the current project."""
    project_id = current.get("project_id")
    q = select(CGSnapshot)
    if current.get("role") != "admin":
        q = q.where(CGSnapshot.project_id == project_id)
    snaps = db.scalars(q.order_by(CGSnapshot.created_at.desc())).all()
    return {"cgsnapshots": [_cgs_to_dict(s) for s in snaps]}


@router.get("/cgsnapshots/{ref}")
def get_cgsnapshot(ref: str, _: CurrentUser, db: DB):
    snap = db.scalar(select(CGSnapshot).where(
        (CGSnapshot.id == ref) | (CGSnapshot.name == ref)
    ))
    if not snap:
        raise HTTPException(status_code=404, detail=f"CG snapshot '{ref}' not found")
    return _cgs_to_dict(snap)


@router.delete("/cgsnapshots/{ref}", status_code=202)
def delete_cgsnapshot(ref: str, _: CurrentUser, db: DB):
    snap = db.scalar(select(CGSnapshot).where(
        (CGSnapshot.id == ref) | (CGSnapshot.name == ref)
    ))
    if not snap:
        raise HTTPException(status_code=404, detail=f"CG snapshot '{ref}' not found")
    db.delete(snap)
    db.commit()
    return {"status": "deleted", "id": snap.id}
