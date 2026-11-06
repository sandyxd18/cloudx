from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.domain import can_see
from .models import Flavor, Keypair, Server, ServerGroup


def get_server(db: Session, ref: str):
    return db.get(Server, ref) or db.scalar(select(Server).where(Server.name == ref))


def get_active_server_by_name(db: Session, name: str):
    return db.scalar(select(Server).where(Server.name == name, Server.status != "DELETED"))


def free_deleted_server_name(db: Session, name: str) -> None:
    deleted = db.scalar(select(Server).where(Server.name == name, Server.status == "DELETED"))
    if deleted:
        deleted.name = f"{deleted.name}-deleted-{deleted.id[:8]}"
        db.commit()


def server_port_name(server_name: str, server_id: str) -> str:
    return f"{server_name}-{server_id[:8]}-port"


def require_server(db: Session, ref: str, current: dict | None = None):
    """current=None skips the project check (internal callers). Other projects' servers look like 404."""
    server = get_server(db, ref)
    if server and current and not can_see(current, server.project_id):
        server = None
    if not server:
        raise HTTPException(status_code=404, detail=f"server '{ref}' not found")
    return server


def require_unlocked_server(db: Session, ref: str, current: dict):
    """require_server for an action that changes the server: a locked server refuses a member. Admins pass (the internal
    token acts as admin), so does an owner who unlocks first."""
    server = require_server(db, ref, current)
    if server.locked and current.get("role") != "admin":
        raise HTTPException(status_code=409, detail="server is locked; unlock it first")
    return server


def require_server_group(db: Session, ref: str, current: dict) -> ServerGroup:
    """A server group by id, when it has no project or the caller may see its project (the project, the domain admin of
    its domain, the system admin); else by name in the caller's project. 404 otherwise, the same for both."""
    group = db.get(ServerGroup, ref)
    if group:
        if group.project_id and not can_see(current, group.project_id):
            group = None
    else:
        group = db.scalar(select(ServerGroup).where(ServerGroup.name == ref, ServerGroup.project_id == current["project_id"]))
    if not group:
        raise HTTPException(status_code=404, detail="server group not found")
    return group


def get_flavor(db: Session, ref: str):
    return db.get(Flavor, ref) or db.scalar(select(Flavor).where(Flavor.name == ref))


def require_flavor(db: Session, ref: str, current: dict | None = None):
    """current=None skips the visibility check (internal callers). A flavor that is not public is for admins only."""
    flavor = get_flavor(db, ref)
    if flavor and current is not None and current.get("role") != "admin" and not flavor.is_public:
        flavor = None
    if not flavor:
        raise HTTPException(status_code=404, detail=f"flavor '{ref}' not found")
    return flavor


def get_keypair(db: Session, ref: str):
    return db.get(Keypair, ref) or db.scalar(select(Keypair).where(Keypair.name == ref))


def require_keypair(db: Session, ref: str):
    keypair = get_keypair(db, ref)
    if not keypair:
        raise HTTPException(status_code=404, detail=f"keypair '{ref}' not found")
    return keypair
