import os
import shutil
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated

import requests
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, Integer, String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://mini:mini@postgres:5432/mini_openstack")
COMPUTE_API_URL = os.getenv("COMPUTE_API_URL", "http://compute-api:8000")
VOLUME_STORE_PATH = Path(os.getenv("VOLUME_STORE_PATH", "/var/lib/mini-openstack/volumes"))

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def new_id() -> str:
    return str(uuid.uuid4())


class Volume(Base):
    __tablename__ = "block_store_volumes"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String, unique=True, index=True)
    size_gb: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String, default="available")
    attached_to_server_id: Mapped[str | None] = mapped_column(String, nullable=True)
    path: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class VolumeIn(BaseModel):
    name: str
    size_gb: int = Field(gt=0)


class AttachIn(BaseModel):
    server: str


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


DB = Annotated[Session, Depends(get_db)]
app = FastAPI(title="mini OpenStack lab block-store-api", version="0.1.0")


@app.on_event("startup")
def startup():
    VOLUME_STORE_PATH.mkdir(parents=True, exist_ok=True)
    VOLUME_STORE_PATH.chmod(0o755)
    Base.metadata.create_all(bind=engine)


def obj_to_dict(obj):
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}


def get_volume(db: Session, ref: str):
    return db.get(Volume, ref) or db.scalar(select(Volume).where(Volume.name == ref))


def require_volume(db: Session, ref: str):
    volume = get_volume(db, ref)
    if not volume:
        raise HTTPException(status_code=404, detail=f"volume '{ref}' not found")
    return volume


def get_server(ref: str):
    try:
        response = requests.get(f"{COMPUTE_API_URL}/servers/{ref}", timeout=8)
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=response.json().get("detail", response.text))
    return response.json()


def compute_post(path: str, payload: dict):
    try:
        response = requests.post(f"{COMPUTE_API_URL}{path}", json=payload, timeout=60)
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise HTTPException(status_code=response.status_code, detail=detail)
    return response.json()


@app.get("/health")
def health():
    return {"status": "ok", "service": "block-store-api"}


@app.post("/volumes", status_code=201)
def create_volume(payload: VolumeIn, db: DB):
    if get_volume(db, payload.name):
        raise HTTPException(status_code=409, detail="volume already exists")
    volume_id = new_id()
    path = VOLUME_STORE_PATH / f"{volume_id}.qcow2"
    if shutil.which("qemu-img"):
        subprocess.run(["qemu-img", "create", "-f", "qcow2", str(path), f"{payload.size_gb}G"], check=True)
    else:
        path = VOLUME_STORE_PATH / f"{volume_id}.raw"
        subprocess.run(["truncate", "-s", f"{payload.size_gb}G", str(path)], check=True)
    path.chmod(0o666)
    volume = Volume(id=volume_id, name=payload.name, size_gb=payload.size_gb, status="available", path=str(path))
    db.add(volume)
    db.commit()
    db.refresh(volume)
    return obj_to_dict(volume)


@app.get("/volumes")
def list_volumes(db: DB):
    return {"items": [obj_to_dict(v) for v in db.scalars(select(Volume).order_by(Volume.created_at)).all()]}


@app.get("/volumes/{ref}")
def show_volume(ref: str, db: DB):
    return obj_to_dict(require_volume(db, ref))


@app.post("/volumes/{ref}/attach")
def attach_volume(ref: str, payload: AttachIn, db: DB):
    volume = require_volume(db, ref)
    if volume.status == "in-use":
        raise HTTPException(status_code=409, detail="volume is already attached")
    server = get_server(payload.server)
    if server.get("status") != "ACTIVE":
        raise HTTPException(status_code=409, detail=f"VM must be ACTIVE before attach; current status is {server.get('status')}")
    compute_post(
        f"/servers/{server['id']}/volumes/attach",
        {"volume_id": volume.id, "volume_name": volume.name, "path": volume.path},
    )
    volume.attached_to_server_id = server["id"]
    volume.status = "in-use"
    db.commit()
    db.refresh(volume)
    return obj_to_dict(volume)


@app.post("/volumes/{ref}/detach")
def detach_volume(ref: str, db: DB):
    volume = require_volume(db, ref)
    if volume.status != "in-use" or not volume.attached_to_server_id:
        raise HTTPException(status_code=409, detail="volume is not attached")
    compute_post(
        f"/servers/{volume.attached_to_server_id}/volumes/detach",
        {"volume_id": volume.id, "volume_name": volume.name, "path": volume.path},
    )
    volume.attached_to_server_id = None
    volume.status = "available"
    db.commit()
    db.refresh(volume)
    return obj_to_dict(volume)


@app.post("/volumes/{ref}/force-detach")
def force_detach_volume(ref: str, db: DB):
    volume = require_volume(db, ref)
    volume.attached_to_server_id = None
    volume.status = "available"
    db.commit()
    db.refresh(volume)
    return obj_to_dict(volume)


@app.post("/servers/{server_id}/volumes/detach-all")
def detach_server_volumes(server_id: str, db: DB):
    volumes = db.scalars(select(Volume).where(Volume.attached_to_server_id == server_id)).all()
    detached = []
    for volume in volumes:
        volume.attached_to_server_id = None
        volume.status = "available"
        detached.append(obj_to_dict(volume))
    db.commit()
    return {"server_id": server_id, "detached": detached}


@app.delete("/volumes/{ref}")
def delete_volume(ref: str, db: DB):
    volume = require_volume(db, ref)
    if volume.status == "in-use":
        raise HTTPException(status_code=409, detail="detach volume before delete")
    path = Path(volume.path)
    if path.exists():
        path.unlink()
    db.delete(volume)
    db.commit()
    return {"status": "deleted", "id": volume.id, "name": volume.name}
