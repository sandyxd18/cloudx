from fastapi import APIRouter, HTTPException
from shared.auth_middleware import AdminUser, CurrentUser
from sqlalchemy import select

from ..db import DB
from ..models import DISK_TYPES, Volume, VolumeType
from ..schemas import VolumeTypeIn, VolumeTypeUpdateIn

router = APIRouter()


# ── Volume Types ───────────────────────────────────────────────────────────────

def _vt_to_dict(vt: VolumeType) -> dict:
    return {c.name: getattr(vt, c.name) for c in vt.__table__.columns}


@router.post("/volume-types", status_code=201)
def create_volume_type(payload: VolumeTypeIn, _: AdminUser, db: DB):
    if payload.disk_type not in DISK_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid disk_type '{payload.disk_type}'. Valid values: {', '.join(DISK_TYPES)}"
        )
    if payload.backend not in ("lvm", "ceph"):
        raise HTTPException(status_code=400, detail="backend must be 'lvm' or 'ceph'")
    if db.scalar(select(VolumeType).where(VolumeType.name == payload.name)):
        raise HTTPException(status_code=409, detail=f"volume type '{payload.name}' already exists")
    vt = VolumeType(**payload.model_dump())
    db.add(vt)
    db.commit()
    db.refresh(vt)
    return _vt_to_dict(vt)


@router.get("/volume-types")
def list_volume_types(db: DB, _: CurrentUser = None):
    return {"items": [_vt_to_dict(vt) for vt in db.scalars(select(VolumeType).order_by(VolumeType.name)).all()]}


@router.get("/volume-types/{ref}")
def show_volume_type(ref: str, _: CurrentUser, db: DB):
    vt = db.get(VolumeType, ref) or db.scalar(select(VolumeType).where(VolumeType.name == ref))
    if not vt:
        raise HTTPException(status_code=404, detail=f"volume type '{ref}' not found")
    return _vt_to_dict(vt)


@router.patch("/volume-types/{ref}")
def update_volume_type(ref: str, payload: VolumeTypeUpdateIn, _: AdminUser, db: DB):
    vt = db.get(VolumeType, ref) or db.scalar(select(VolumeType).where(VolumeType.name == ref))
    if not vt:
        raise HTTPException(status_code=404, detail=f"volume type '{ref}' not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(vt, field, value)
    db.commit()
    db.refresh(vt)
    return _vt_to_dict(vt)


@router.delete("/volume-types/{ref}")
def delete_volume_type(ref: str, _: AdminUser, db: DB):
    vt = db.get(VolumeType, ref) or db.scalar(select(VolumeType).where(VolumeType.name == ref))
    if not vt:
        raise HTTPException(status_code=404, detail=f"volume type '{ref}' not found")
    in_use = db.scalar(select(Volume).where(Volume.volume_type == vt.name))
    if in_use:
        raise HTTPException(status_code=409, detail=f"volume type '{vt.name}' is in use by existing volumes")
    db.delete(vt)
    db.commit()
    return {"status": "deleted", "name": vt.name}
