from fastapi import APIRouter, HTTPException
from shared.auth_middleware import CurrentUser
from sqlalchemy import select

from ..db import DB
from ..models import ServerGroup
from ..schemas import ServerGroupIn
from ..serializers import obj_to_dict

router = APIRouter()


# ── Server Groups ─────────────────────────────────────────────────────────────
@router.post("/server-groups", status_code=201)
def create_server_group(payload: ServerGroupIn, current: CurrentUser, db: DB):
    sg = ServerGroup(
        name=payload.name,
        policy=payload.policy,
        project_id=current["project_id"],
    )
    db.add(sg)
    db.commit()
    db.refresh(sg)
    return {**obj_to_dict(sg), "members": []}


@router.get("/server-groups")
def list_server_groups(current: CurrentUser, db: DB):
    q = select(ServerGroup).where(ServerGroup.project_id == current["project_id"])
    groups = db.scalars(q).all()
    return {"items": [{**obj_to_dict(g), "members": [m for m in (g.members or "").split(",") if m]} for g in groups]}


@router.get("/server-groups/{ref}")
def show_server_group(ref: str, current: CurrentUser, db: DB):
    sg = db.get(ServerGroup, ref) or db.scalar(
        select(ServerGroup).where(ServerGroup.name == ref, ServerGroup.project_id == current["project_id"]))
    if not sg:
        raise HTTPException(status_code=404, detail="server group not found")
    return {**obj_to_dict(sg), "members": [m for m in (sg.members or "").split(",") if m]}


@router.delete("/server-groups/{ref}", status_code=204)
def delete_server_group(ref: str, current: CurrentUser, db: DB):
    sg = db.get(ServerGroup, ref) or db.scalar(
        select(ServerGroup).where(ServerGroup.name == ref, ServerGroup.project_id == current["project_id"]))
    if not sg:
        raise HTTPException(status_code=404, detail="server group not found")
    db.delete(sg)
    db.commit()
