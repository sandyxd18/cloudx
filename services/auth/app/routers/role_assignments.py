from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from ..db import DB
from ..deps import CurrentUser, DomainAdminUser, scope_domain
from ..lookups import _scoped_project, _scoped_user
from ..models import RoleAssignment, User
from ..schemas import ROLES

router = APIRouter()


def _out(a: RoleAssignment) -> dict:
    return {"user_id": a.user_id, "project_id": a.project_id, "role": a.role}


@router.put("/projects/{project}/users/{user}/roles/{role}", status_code=204)
def assign_role(project: str, user: str, role: str, current: DomainAdminUser, db: DB):
    if role not in ROLES:
        raise HTTPException(status_code=400, detail=f"role must be one of: {', '.join(ROLES)}")
    if role == "admin" and scope_domain(current) is not None:
        raise HTTPException(status_code=403, detail="a domain admin cannot grant the admin role")
    p, u = _scoped_project(db, project, current), _scoped_user(db, user, current)
    if p.domain_id != u.domain_id:
        raise HTTPException(status_code=409, detail="user and project belong to different domains")
    db.merge(RoleAssignment(user_id=u.id, project_id=p.id, role=role))
    db.commit()


@router.delete("/projects/{project}/users/{user}/roles/{role}", status_code=204)
def unassign_role(project: str, user: str, role: str, current: DomainAdminUser, db: DB):
    p, u = _scoped_project(db, project, current), _scoped_user(db, user, current)
    a = db.get(RoleAssignment, (u.id, p.id))
    if not a or a.role != role:
        raise HTTPException(status_code=404, detail="role assignment not found")
    db.delete(a)
    db.commit()


@router.get("/role-assignments")
def list_role_assignments(current: CurrentUser, db: DB, project: str | None = None, user: str | None = None):
    q = select(RoleAssignment)
    if current.get("role") != "admin" and not current.get("domain_admin"):
        q = q.where(RoleAssignment.user_id == current["sub"])  # members only see their own
    else:
        if scope_domain(current) is not None:
            q = q.where(RoleAssignment.user_id.in_(select(User.id).where(User.domain_id == scope_domain(current))))
        if user:
            q = q.where(RoleAssignment.user_id == _scoped_user(db, user, current).id)
    if project:
        q = q.where(RoleAssignment.project_id == _scoped_project(db, project, current).id)
    return {"items": [_out(a) for a in db.scalars(q).all()]}
