from fastapi import APIRouter, HTTPException
from shared.auth_middleware import AdminUser, CurrentUser
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import DB
from ..models import Snapshot, Volume, VolumeQuota
from ..schemas import QuotaIn

router = APIRouter()


def _quota_usage(db: Session, project_id: str) -> dict:
    """Return current usage for a project."""
    vols = db.scalars(
        select(Volume).where(
            Volume.project_id == project_id,
            Volume.status.not_in(["deleting", "error"]),
        )
    ).all()
    snaps_count = db.scalar(
        select(Snapshot).where(Snapshot.status.not_in(["deleting", "error"]))
    )
    return {
        "volumes": len(vols),
        "gigabytes": sum(v.size_gb for v in vols),
        "snapshots": len(db.scalars(select(Snapshot)).all()),
    }


def _quota_to_dict(q: VolumeQuota, usage: dict | None = None) -> dict:
    d = {
        "project_id": q.project_id,
        "volumes":    q.volumes,
        "gigabytes":  q.gigabytes,
        "snapshots":  q.snapshots,
    }
    if usage:
        d["usage"] = usage
    return d


@router.get("/os-quota-sets/{project_id}")
def get_quota(project_id: str, current: CurrentUser, db: DB, usage: bool = False):
    """Get quota limits for a project."""
    if current.get("role") != "admin" and current.get("project_id") != project_id:
        raise HTTPException(status_code=403, detail="forbidden")
    quota = db.get(VolumeQuota, project_id)
    if not quota:
        # Return defaults
        quota = VolumeQuota(project_id=project_id, volumes=10, gigabytes=1000, snapshots=10)
    u = _quota_usage(db, project_id) if usage else None
    return _quota_to_dict(quota, u)


@router.put("/os-quota-sets/{project_id}")
def update_quota(project_id: str, payload: QuotaIn, _: AdminUser, db: DB):
    """Set quota limits for a project (admin only)."""
    quota = db.get(VolumeQuota, project_id)
    if not quota:
        quota = VolumeQuota(project_id=project_id)
        db.add(quota)
    if payload.volumes is not None:
        quota.volumes = payload.volumes
    if payload.gigabytes is not None:
        quota.gigabytes = payload.gigabytes
    if payload.snapshots is not None:
        quota.snapshots = payload.snapshots
    db.commit()
    db.refresh(quota)
    return _quota_to_dict(quota)


@router.delete("/os-quota-sets/{project_id}", status_code=204)
def delete_quota(project_id: str, _: AdminUser, db: DB):
    """Reset quota to defaults by deleting the override row."""
    quota = db.get(VolumeQuota, project_id)
    if quota:
        db.delete(quota)
        db.commit()


@router.get("/os-quota-sets/{project_id}/defaults")
def get_quota_defaults(project_id: str, _: CurrentUser, db: DB):
    """Return the default quota values."""
    return {"project_id": project_id, "volumes": 10, "gigabytes": 1000, "snapshots": 10}
