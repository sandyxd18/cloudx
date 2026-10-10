from fastapi import APIRouter, HTTPException
from shared.auth_middleware import AdminUser, CurrentUser
from sqlalchemy import select

from ..db import DB
from ..models import SubnetPool
from ..schemas import SubnetPoolIn
from ..serializers import get_by_ref

router = APIRouter()

def _pool_to_dict(pool: SubnetPool) -> dict:
    return {
        "id": pool.id,
        "name": pool.name,
        "prefixes": pool.prefixes.split(",") if pool.prefixes else [],
        "default_prefixlen": pool.default_prefixlen,
        "min_prefixlen": pool.min_prefixlen,
        "max_prefixlen": pool.max_prefixlen,
        "shared": pool.shared,
        "project_id": pool.project_id,
        "created_at": pool.created_at.isoformat(),
    }

@router.post("/subnetpools", status_code=201)
def create_subnet_pool(payload: SubnetPoolIn, current: CurrentUser, db: DB):
    if get_by_ref(db, SubnetPool, payload.name, current=current):
        raise HTTPException(status_code=409, detail="subnet pool already exists")
    pool = SubnetPool(
        name=payload.name,
        prefixes=",".join(payload.prefixes),
        default_prefixlen=payload.default_prefixlen,
        min_prefixlen=payload.min_prefixlen,
        max_prefixlen=payload.max_prefixlen,
        shared=payload.shared,
        project_id=current.get("project_id"),
    )
    db.add(pool)
    db.commit()
    db.refresh(pool)
    return _pool_to_dict(pool)

@router.get("/subnetpools")
def list_subnet_pools(current: CurrentUser, db: DB):
    q = select(SubnetPool)
    if current.get("role") != "admin":
        q = q.where(
            (SubnetPool.shared == True) | (SubnetPool.project_id == current.get("project_id"))
        )
    return {"subnet_pools": [_pool_to_dict(p) for p in db.scalars(q).all()]}

@router.get("/subnetpools/{pool_id}")
def show_subnet_pool(pool_id: str, current: CurrentUser, db: DB):
    pool = get_by_ref(db, SubnetPool, pool_id, current=current)
    if not pool:
        raise HTTPException(status_code=404, detail="subnet pool not found")
    return _pool_to_dict(pool)

@router.delete("/subnetpools/{pool_id}", status_code=204)
def delete_subnet_pool(pool_id: str, _: AdminUser, db: DB):
    pool = db.get(SubnetPool, pool_id)
    if not pool:
        raise HTTPException(status_code=404, detail="subnet pool not found")
    db.delete(pool)
    db.commit()
