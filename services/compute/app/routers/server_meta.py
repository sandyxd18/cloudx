from fastapi import APIRouter, HTTPException
from shared.auth_middleware import CurrentUser
from sqlalchemy import select, text

from ..db import DB
from ..lookups import require_server
from ..models import ServerTag

router = APIRouter()


# ── Server Lock / Unlock ───────────────────────────────────────────────────────

@router.post("/servers/{ref}/lock")
def lock_server(ref: str, current: CurrentUser, db: DB):
    """Lock a server so only admins can perform destructive operations."""
    server = require_server(db, ref)
    server.locked = True
    server.locked_by = current.get("username") or current.get("sub")
    db.commit()
    db.refresh(server)
    return {"server": server.name, "locked": True, "locked_by": server.locked_by}


@router.post("/servers/{ref}/unlock")
def unlock_server(ref: str, current: CurrentUser, db: DB):
    """Unlock a server."""
    server = require_server(db, ref)
    # Only admin or the user who locked it can unlock
    if server.locked and current.get("role") != "admin" and server.locked_by != (current.get("username") or current.get("sub")):
        raise HTTPException(status_code=403, detail="only the locking user or an admin can unlock this server")
    server.locked = False
    server.locked_by = None
    db.commit()
    db.refresh(server)
    return {"server": server.name, "locked": False}


# ── Server Tags ────────────────────────────────────────────────────────────────

@router.get("/servers/{ref}/tags")
def list_server_tags(ref: str, _: CurrentUser, db: DB):
    """List all tags for a server."""
    server = require_server(db, ref)
    tags = db.scalars(select(ServerTag).where(ServerTag.server_id == server.id)).all()
    return {"tags": [t.tag for t in tags]}


@router.put("/servers/{ref}/tags")
def replace_server_tags(ref: str, payload: dict, _: CurrentUser, db: DB):
    """Replace all tags on a server."""
    server = require_server(db, ref)
    new_tags = payload.get("tags", [])
    if not isinstance(new_tags, list):
        raise HTTPException(status_code=400, detail="'tags' must be a list of strings")
    # Delete existing and add new
    db.execute(text("DELETE FROM compute_server_tags WHERE server_id = :sid"), {"sid": server.id})
    for tag in new_tags:
        db.add(ServerTag(server_id=server.id, tag=str(tag)))
    db.commit()
    return {"tags": new_tags}


@router.put("/servers/{ref}/tags/{tag}")
def add_server_tag(ref: str, tag: str, _: CurrentUser, db: DB):
    """Add a single tag to a server."""
    server = require_server(db, ref)
    existing = db.get(ServerTag, (server.id, tag))
    if not existing:
        db.add(ServerTag(server_id=server.id, tag=tag))
        db.commit()
    return {"server": server.name, "tag": tag}


@router.delete("/servers/{ref}/tags/{tag}", status_code=204)
def delete_server_tag(ref: str, tag: str, _: CurrentUser, db: DB):
    """Remove a single tag from a server."""
    server = require_server(db, ref)
    row = db.get(ServerTag, (server.id, tag))
    if row:
        db.delete(row)
        db.commit()
