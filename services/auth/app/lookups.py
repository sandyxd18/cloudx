from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Group, Project, User


def _get_user_by_ref(db: Session, ref: str) -> User:
    u = db.get(User, ref) or db.scalar(select(User).where(User.username == ref))
    if not u:
        raise HTTPException(status_code=404, detail="user not found")
    return u


def _get_project_by_ref(db: Session, ref: str) -> Project:
    p = db.get(Project, ref) or db.scalar(select(Project).where(Project.name == ref))
    if not p:
        raise HTTPException(status_code=404, detail="project not found")
    return p


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
