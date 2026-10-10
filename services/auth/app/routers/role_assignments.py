from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from ..db import DB
from ..deps import AdminUser, CurrentUser
from ..lookups import _get_project_by_ref, _get_user_by_ref
from ..models import RoleAssignment
from ..schemas import ROLES

router = APIRouter()


def _out(a: RoleAssignment) -> dict:
    return {"user_id": a.user_id, "project_id": a.project_id, "role": a.role}


@router.put("/projects/{project}/users/{user}/roles/{role}", status_code=204)
def assign_role(project: str, user: str, role: str, _: AdminUser, db: DB):
    if role not in ROLES:
        raise HTTPException(status_code=400, detail=f"role must be one of: {', '.join(ROLES)}")
    p, u = _get_project_by_ref(db, project), _get_user_by_ref(db, user)
    db.merge(RoleAssignment(user_id=u.id, project_id=p.id, role=role))
    db.commit()


@router.delete("/projects/{project}/users/{user}/roles/{role}", status_code=204)
def unassign_role(project: str, user: str, role: str, _: AdminUser, db: DB):
    p, u = _get_project_by_ref(db, project), _get_user_by_ref(db, user)
    a = db.get(RoleAssignment, (u.id, p.id))
    if not a or a.role != role:
        raise HTTPException(status_code=404, detail="role assignment not found")
    db.delete(a)
    db.commit()


@router.get("/role-assignments")
def list_role_assignments(current: CurrentUser, db: DB, project: str | None = None, user: str | None = None):
    q = select(RoleAssignment)
    if current.get("role") != "admin":
        q = q.where(RoleAssignment.user_id == current["sub"])  # members only see their own
    elif user:
        q = q.where(RoleAssignment.user_id == _get_user_by_ref(db, user).id)
    if project:
        q = q.where(RoleAssignment.project_id == _get_project_by_ref(db, project).id)
    return {"items": [_out(a) for a in db.scalars(q).all()]}
