from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .deps import in_scope, scope_domain
from .models import Group, Project, User


def _by_name(db: Session, model, col, ref: str, domain: str | None, what: str):
    """Id first, then name inside `domain`. Names are unique per domain only, so with no domain an
    ambiguous name is a 409 (use the id or pass a domain)."""
    row = db.get(model, ref)
    if row:
        return row
    q = select(model).where(col == ref)
    if domain is not None:
        q = q.where(model.domain_id == domain)
    rows = db.scalars(q.limit(2)).all()
    if not rows:
        raise HTTPException(status_code=404, detail=f"{what} not found")
    if len(rows) > 1:
        raise HTTPException(status_code=409, detail=f"{what} name is ambiguous across domains; use the id or a domain")
    return rows[0]

def _get_user_by_ref(db: Session, ref: str, domain: str | None = None) -> User:
    return _by_name(db, User, User.username, ref, domain, "user")

def _get_project_by_ref(db: Session, ref: str, domain: str | None = None) -> Project:
    return _by_name(db, Project, Project.name, ref, domain, "project")


def _scoped_project(db: Session, ref: str, current: dict) -> Project:
    """Project lookup that hides other domains from a domain admin (404)."""
    p = _get_project_by_ref(db, ref, scope_domain(current))
    if not in_scope(current, p.domain_id):
        raise HTTPException(status_code=404, detail="project not found")
    return p

def _scoped_user(db: Session, ref: str, current: dict) -> User:
    u = _get_user_by_ref(db, ref, scope_domain(current))
    if not in_scope(current, u.domain_id):
        raise HTTPException(status_code=404, detail="user not found")
    return u

def _get_group_by_ref(db: Session, ref: str, project_id: str | None = None) -> Group:
    g = db.get(Group, ref)
    if not g:
        q = select(Group).where(Group.name == ref)
        if project_id:
            q = q.where(Group.project_id == project_id)
        g = db.scalar(q)
    if not g:
        raise HTTPException(status_code=404, detail="group not found")
    return g
