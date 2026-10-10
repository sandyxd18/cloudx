from fastapi import APIRouter, HTTPException, Query
from shared.auth_middleware import InternalAuth
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import ADMIN_PROJECT
from ..db import DB
from ..deps import AdminUser, CurrentUser
from ..lookups import _get_project_by_ref
from ..models import Group, GroupMember, Project, ProjectQuota, User, utcnow
from ..schemas import ProjectIn, ProjectUpdate, QuotaUpdate
from ..serializers import _project_to_dict

router = APIRouter()


# ── Projects ───────────────────────────────────────────────────────────────────
@router.post("/projects", status_code=201)
def create_project(payload: ProjectIn, _: AdminUser, db: DB):
    if db.scalar(select(Project).where(Project.name == payload.name)):
        raise HTTPException(status_code=409, detail="project already exists")
    p = Project(**payload.model_dump())
    db.add(p)
    db.commit()
    db.refresh(p)
    return _project_to_dict(p)


@router.get("/projects")
def list_projects(
    current: CurrentUser, db: DB,
    limit: int = Query(default=100, ge=1, le=1000),
    marker: str | None = None,
):
    if current.get("role") == "admin":
        q = select(Project).order_by(Project.created_at)
    else:
        user = db.get(User, current["sub"])
        project_ids = set()
        if user and user.project_id:
            project_ids.add(user.project_id)
        group_pids = db.scalars(
            select(Group.project_id)
            .join(GroupMember, Group.id == GroupMember.group_id)
            .where(GroupMember.user_id == current["sub"])
        ).all()
        project_ids.update(group_pids)
        if not project_ids:
            return {"items": []}
        q = select(Project).where(Project.id.in_(project_ids)).order_by(Project.created_at)

    if marker:
        ref = db.get(Project, marker)
        if ref:
            q = q.where(Project.created_at > ref.created_at)
    q = q.limit(limit)
    return {"items": [_project_to_dict(p) for p in db.scalars(q).all()]}


@router.get("/projects/{ref}")
def show_project(ref: str, current: CurrentUser, db: DB):
    p = _get_project_by_ref(db, ref)
    if current.get("role") != "admin":
        user = db.get(User, current["sub"])
        allowed = set()
        if user and user.project_id:
            allowed.add(user.project_id)
        group_pids = db.scalars(
            select(Group.project_id)
            .join(GroupMember, Group.id == GroupMember.group_id)
            .where(GroupMember.user_id == current["sub"])
        ).all()
        allowed.update(group_pids)
        if p.id not in allowed:
            raise HTTPException(status_code=403, detail="forbidden")
    return _project_to_dict(p)


@router.put("/projects/{ref}")
def update_project(ref: str, payload: ProjectUpdate, _: AdminUser, db: DB):
    p = _get_project_by_ref(db, ref)
    for k, v in payload.model_dump(exclude_none=True).items():
        setattr(p, k, v)
    db.commit()
    db.refresh(p)
    return _project_to_dict(p)


@router.delete("/projects/{ref}", status_code=204)
def delete_project(ref: str, _: AdminUser, db: DB):
    p = _get_project_by_ref(db, ref)
    if p.name == ADMIN_PROJECT:
        raise HTTPException(status_code=400, detail="cannot delete admin project")
    db.delete(p)
    db.commit()


def _get_or_create_quota(db: Session, project_id: str) -> ProjectQuota:
    quota = db.get(ProjectQuota, project_id)
    if not quota:
        quota = ProjectQuota(project_id=project_id, max_instances=10, max_vcpus=20, max_ram_mb=32768, max_volume_gb=100)
        db.add(quota)
        db.commit()
        db.refresh(quota)
    return quota


QUOTA_FIELDS = ("max_instances", "max_vcpus", "max_ram_mb", "max_volume_gb", "max_networks", "max_subnets", "max_ports", "max_routers", "max_floating_ips", "max_security_groups")


def _quota_dict(project_id: str, q) -> dict:
    return {"project_id": project_id, **{f: getattr(q, f) for f in QUOTA_FIELDS}}


@router.get("/internal/projects/{ref}/quota", dependencies=[InternalAuth])
def internal_project_quota(ref: str, db: DB):
    p = _get_project_by_ref(db, ref)
    return _quota_dict(p.id, _get_or_create_quota(db, p.id))


@router.get("/projects/{ref}/quota")
def get_project_quota(ref: str, _: CurrentUser, db: DB):
    p = _get_project_by_ref(db, ref)
    q = _get_or_create_quota(db, p.id)
    return _quota_dict(p.id, q)


@router.put("/projects/{ref}/quota")
def update_project_quota(ref: str, payload: QuotaUpdate, _: AdminUser, db: DB):
    p = _get_project_by_ref(db, ref)
    q = _get_or_create_quota(db, p.id)
    for k, v in payload.model_dump(exclude_none=True).items():
        setattr(q, k, v)
    q.updated_at = utcnow()
    db.commit()
    db.refresh(q)
    return _quota_dict(p.id, q)
