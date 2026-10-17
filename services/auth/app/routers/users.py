from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from ..config import ADMIN_USERNAME
from ..db import DB
from ..deps import CurrentUser, DomainAdminUser, scope_domain
from ..lookups import _get_user_by_ref, _scoped_project, _scoped_user
from ..models import User
from ..schemas import UserIn, UserUpdate
from ..serializers import _user_to_dict
from ..tokens import _hash_password

router = APIRouter()


# ── Users ──────────────────────────────────────────────────────────────────────
@router.post("/users", status_code=201)
def create_user(payload: UserIn, current: DomainAdminUser, db: DB):
    scope = scope_domain(current)
    if scope is not None and (payload.role == "admin" or payload.is_domain_admin):
        raise HTTPException(status_code=403, detail="a domain admin cannot create admin users")
    if db.scalar(select(User).where(User.username == payload.username)):
        raise HTTPException(status_code=409, detail="username already exists")
    if db.scalar(select(User).where(User.email == payload.email)):
        raise HTTPException(status_code=409, detail="email already exists")
    proj = _scoped_project(db, payload.project_id, current)
    u = User(
        username=payload.username,
        email=payload.email,
        password_hash=_hash_password(payload.password),
        project_id=proj.id,
        role=payload.role,
        domain_id=proj.domain_id,
        is_domain_admin=payload.is_domain_admin,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return _user_to_dict(u)


@router.get("/users")
def list_users(
    current: DomainAdminUser, db: DB,
    limit: int = Query(default=100, ge=1, le=1000),
    marker: str | None = None,
):
    q = select(User).order_by(User.created_at)
    if scope_domain(current) is not None:
        q = q.where(User.domain_id == scope_domain(current))
    if marker:
        ref = db.get(User, marker)
        if ref:
            q = q.where(User.created_at > ref.created_at)
    q = q.limit(limit)
    return {"items": [_user_to_dict(u) for u in db.scalars(q).all()]}


@router.get("/users/{ref}")
def show_user(ref: str, current: CurrentUser, db: DB):
    u = _scoped_user(db, ref, current) if current.get("domain_admin") else _get_user_by_ref(db, ref)
    # Non-admins can only see themselves (a domain admin also sees its domain, via _scoped_user)
    if current.get("role") != "admin" and not current.get("domain_admin") and u.id != current.get("sub"):
        raise HTTPException(status_code=403, detail="forbidden")
    return _user_to_dict(u)


@router.put("/users/{ref}")
def update_user(ref: str, payload: UserUpdate, current: CurrentUser, db: DB):
    is_sys = current.get("role") == "admin"
    u = _scoped_user(db, ref, current) if current.get("domain_admin") else _get_user_by_ref(db, ref)
    if not is_sys and not current.get("domain_admin") and u.id != current.get("sub"):
        raise HTTPException(status_code=403, detail="forbidden")
    if payload.role and (not is_sys and (payload.role == "admin" or not current.get("domain_admin"))):
        raise HTTPException(status_code=403, detail="only admin can change roles")
    if payload.is_domain_admin is not None and not is_sys:
        raise HTTPException(status_code=403, detail="only a system admin can change domain admins")
    data = payload.model_dump(exclude_none=True)
    if "password" in data:
        u.password_hash = _hash_password(data.pop("password"))
    for k, v in data.items():
        setattr(u, k, v)
    db.commit()
    db.refresh(u)
    return _user_to_dict(u)


@router.delete("/users/{ref}", status_code=204)
def delete_user(ref: str, current: DomainAdminUser, db: DB):
    u = _scoped_user(db, ref, current)
    if u.username == ADMIN_USERNAME:
        raise HTTPException(status_code=400, detail="cannot delete admin user")
    db.delete(u)
    db.commit()
