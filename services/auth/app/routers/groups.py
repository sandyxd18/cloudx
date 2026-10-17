from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from ..db import DB
from ..deps import CurrentUser, in_scope, scope_domain
from ..lookups import _get_group_by_ref, _get_user_by_ref
from ..models import Group, GroupMember, Project
from ..schemas import GroupIn
from ..serializers import _group_to_dict

router = APIRouter()


# ── Groups ─────────────────────────────────────────────────────────────────────
def _group(db, ref: str, current: dict) -> Group:
    """Group lookup; a group of another domain is invisible (404) to non-system callers."""
    g = _get_group_by_ref(db, ref, current["project_id"])
    p = db.get(Project, g.project_id)
    if p and not in_scope(current, p.domain_id):
        raise HTTPException(status_code=404, detail="group not found")
    return g

@router.post("/groups", status_code=201)
def create_group(payload: GroupIn, current: CurrentUser, db: DB):
    proj_id = current["project_id"]
    if db.scalar(select(Group).where(Group.name == payload.name, Group.project_id == proj_id)):
        raise HTTPException(status_code=409, detail="group already exists in project")
    g = Group(project_id=proj_id, **payload.model_dump())
    db.add(g)
    db.commit()
    db.refresh(g)
    return _group_to_dict(g)


@router.get("/groups")
def list_groups(current: CurrentUser, db: DB):
    proj_id = current["project_id"]
    if current.get("role") == "admin":
        groups = db.scalars(select(Group)).all()
    elif current.get("domain_admin"):
        groups = db.scalars(select(Group).join(Project, Project.id == Group.project_id)
                            .where(Project.domain_id == scope_domain(current))).all()
    else:
        groups = db.scalars(select(Group).where(Group.project_id == proj_id)).all()
    result = []
    for g in groups:
        members = [m.user_id for m in db.scalars(select(GroupMember).where(GroupMember.group_id == g.id)).all()]
        result.append(_group_to_dict(g, members))
    return {"items": result}


@router.put("/groups/{ref}/members/{user_ref}")
def add_group_member(ref: str, user_ref: str, current: CurrentUser, db: DB):
    g = _group(db, ref, current)
    u = _get_user_by_ref(db, user_ref, scope_domain(current))
    if not in_scope(current, u.domain_id):
        raise HTTPException(status_code=404, detail="user not found")
    if db.get(Project, g.project_id).domain_id != u.domain_id:
        raise HTTPException(status_code=409, detail="user and group belong to different domains")
    exists = db.scalar(select(GroupMember).where(
        GroupMember.group_id == g.id, GroupMember.user_id == u.id))
    if not exists:
        db.add(GroupMember(group_id=g.id, user_id=u.id))
        db.commit()
    return {"group_id": g.id, "user_id": u.id, "status": "member"}


@router.delete("/groups/{ref}/members/{user_ref}", status_code=204)
def remove_group_member(ref: str, user_ref: str, current: CurrentUser, db: DB):
    g = _group(db, ref, current)
    u = _get_user_by_ref(db, user_ref, scope_domain(current))
    m = db.scalar(select(GroupMember).where(
        GroupMember.group_id == g.id, GroupMember.user_id == u.id))
    if m:
        db.delete(m)
        db.commit()


@router.delete("/groups/{ref}", status_code=204)
def delete_group(ref: str, current: CurrentUser, db: DB):
    g = _group(db, ref, current)
    db.delete(g)
    db.commit()
