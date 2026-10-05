from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Flavor, Keypair, Server


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


def require_server(db: Session, ref: str):
    server = get_server(db, ref)
    if not server:
        raise HTTPException(status_code=404, detail=f"server '{ref}' not found")
    return server


def get_flavor(db: Session, ref: str):
    return db.get(Flavor, ref) or db.scalar(select(Flavor).where(Flavor.name == ref))


def require_flavor(db: Session, ref: str):
    flavor = get_flavor(db, ref)
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
