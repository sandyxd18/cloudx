from __future__ import annotations
import os
from shared.config import load_service_config
load_service_config("database-api")

"""cloudx database API (Trove/RDS-like): PostgreSQL and MariaDB on a VM or in a container, built from the other CloudX APIs."""

import hmac
import logging
import threading
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator
from sqlalchemy import DateTime, Integer, String, UniqueConstraint, create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from shared.auth_middleware import CurrentUser
from shared.cors import cors_origins
from shared.db_cloudinit import PORTS
from shared.db_validate import check_backend_storage, check_cidr, check_engine, check_password, check_size, gen_password
from shared.domain import can_see, owns, visible_projects
from shared.request_id_middleware import RequestIDMiddleware
from shared.schema import require_schema

from . import provision

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://cloudx:cloudx@127.0.0.1:5432/cloudx")
PUBLIC_URL = os.getenv("DATABASE_PUBLIC_URL", "http://127.0.0.1:8019").rstrip("/")
ADMIN_USER = {"postgresql": "postgres", "mariadb": "root"}
log = logging.getLogger("database-api")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)

class Base(DeclarativeBase):
    pass

def new_id() -> str:
    return str(uuid.uuid4())

class Instance(Base):
    __tablename__ = "db_instances"
    __table_args__ = (UniqueConstraint("project_id", "name"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String)
    project_id: Mapped[str] = mapped_column(String, index=True)
    engine: Mapped[str] = mapped_column(String)
    backend: Mapped[str] = mapped_column(String)
    storage: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="building")  # building | active | stopped | error | deleting
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    network_id: Mapped[str] = mapped_column(String)
    size_gb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    image: Mapped[str | None] = mapped_column(String, nullable=True)
    flavor: Mapped[str | None] = mapped_column(String, nullable=True)
    allowed_cidr: Mapped[str | None] = mapped_column(String, nullable=True)
    ip: Mapped[str | None] = mapped_column(String, nullable=True)
    sg_id: Mapped[str | None] = mapped_column(String, nullable=True)
    server_id: Mapped[str | None] = mapped_column(String, nullable=True)
    container_id: Mapped[str | None] = mapped_column(String, nullable=True)
    volume_id: Mapped[str | None] = mapped_column(String, nullable=True)
    share_id: Mapped[str | None] = mapped_column(String, nullable=True)
    share_net_id: Mapped[str | None] = mapped_column(String, nullable=True)
    rule_id: Mapped[str | None] = mapped_column(String, nullable=True)
    token_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

class Backup(Base):
    __tablename__ = "db_backups"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    instance_id: Mapped[str | None] = mapped_column(String, nullable=True)  # no FK: the backup outlives the instance
    project_id: Mapped[str] = mapped_column(String, index=True)
    name: Mapped[str] = mapped_column(String)
    engine: Mapped[str] = mapped_column(String)
    size_gb: Mapped[int] = mapped_column(Integer)
    volume_backup_id: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="creating")  # creating | available | error
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

RESTORING: set[str] = set()  # ponytail: in memory; lost on restart (an interrupted restore is already marked error)

app = FastAPI(title="cloudx database-api", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=cors_origins(), allow_methods=["*"], allow_headers=["*"])
if RequestIDMiddleware:
    app.add_middleware(RequestIDMiddleware)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

DB = Annotated[Session, Depends(get_db)]

def run_async(fn, *args):  # tests replace this to run inline
    threading.Thread(target=fn, args=args, daemon=True).start()

def make_api(headers: dict):  # tests replace this with a fake
    return provision.Api(headers)

@app.on_event("startup")
def startup():
    require_schema(engine, "database")
    with SessionLocal() as db:  # a restart killed the worker threads: nothing will finish these rows
        for row in db.scalars(select(Instance).where(Instance.status.in_(("building", "deleting")))):
            row.status, row.error = "error", "interrupted by a database-api restart; delete and recreate"
        for b in db.scalars(select(Backup).where(Backup.status == "creating")):
            b.status, b.error = "error", "interrupted by a database-api restart"
        db.commit()

@app.get("/health")
def health():
    return {"status": "ok"}

def _pid(current: dict) -> str:
    return current.get("project_id") or current.get("tenant_id") or "default"

def _out(i: Instance, password: str | None = None) -> dict:
    d = {"id": i.id, "name": i.name, "project_id": i.project_id, "engine": i.engine, "backend": i.backend,
         "storage": i.storage, "status": i.status, "error": i.error, "network_id": i.network_id, "size_gb": i.size_gb,
         "host": i.ip, "port": PORTS[i.engine], "admin_user": ADMIN_USER[i.engine], "allowed_cidr": i.allowed_cidr,
         "created_at": i.created_at.isoformat() if i.created_at else None}
    if password:
        d["password"] = password
    return d

def _get(db: Session, ref: str, current: dict, write: bool = False) -> Instance:
    i = db.get(Instance, ref)
    if not i or not can_see(current, i.project_id):
        raise HTTPException(404, "Database instance not found")
    if write and not owns(current, i.project_id):
        raise HTTPException(403, "Not allowed")
    return i

class InstanceIn(BaseModel):
    name: str
    engine: str
    backend: str
    network_id: str
    storage: str = "local"
    size_gb: int | None = None
    image: str | None = None
    flavor: str | None = None
    cpu: float = 0.5
    memory_mb: int = 512
    allowed_cidr: str | None = None
    password: str | None = None

    @field_validator("engine")
    @classmethod
    def _engine(cls, v):
        return check_engine(v)

    @field_validator("password")
    @classmethod
    def _pw(cls, v):
        return check_password(v) if v is not None else v

    @field_validator("allowed_cidr")
    @classmethod
    def _cidr(cls, v):
        return check_cidr(v) if v is not None else v

    @field_validator("size_gb")
    @classmethod
    def _size(cls, v):
        return check_size(v) if v is not None else v

def _auth_headers(request: Request) -> dict:
    h = {k: request.headers[k] for k in ("Authorization", "X-Auth-Token") if request.headers.get(k)}
    if not h:
        raise HTTPException(401, "Authorization header required")
    return h

@app.post("/instances", status_code=201)
def create_instance(body: InstanceIn, request: Request, current: CurrentUser, db: DB):
    try:
        check_backend_storage(body.backend, body.storage)
        if body.engine == "postgresql" and body.storage == "file":
            raise ValueError("postgresql on file (NFS) storage is not supported; use block or local, or mariadb")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    if body.storage in ("block", "file") and body.size_gb is None:
        raise HTTPException(422, "size_gb is required for block and file storage")
    if body.backend == "vm" and not (body.image and body.flavor):
        raise HTTPException(422, "image and flavor are required for a vm")
    headers = _auth_headers(request)
    password = body.password or gen_password()
    i = Instance(name=body.name, project_id=_pid(current), engine=body.engine, backend=body.backend, storage=body.storage,
                 network_id=body.network_id, size_gb=body.size_gb, image=body.image, flavor=body.flavor,
                 allowed_cidr=body.allowed_cidr)
    db.add(i)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Database instance name already exists") from None
    spec = {**body.model_dump(), "password": password, "public_url": PUBLIC_URL}
    run_async(provision.build, SessionLocal, Instance, i.id, make_api(headers), spec)
    return _out(i, password)

@app.get("/instances")
def list_instances(current: CurrentUser, db: DB):
    ids = visible_projects(current)
    q = select(Instance).order_by(Instance.created_at)
    if ids is not None:
        q = q.where(Instance.project_id.in_(ids or [_pid(current)]))
    return {"items": [_out(i) for i in db.scalars(q)]}

@app.get("/instances/{iid}")
def get_instance(iid: str, current: CurrentUser, db: DB):
    return _out(_get(db, iid, current))

@app.delete("/instances/{iid}", status_code=202)
def delete_instance(iid: str, request: Request, current: CurrentUser, db: DB):
    i = _get(db, iid, current, write=True)
    if i.status == "deleting":
        raise HTTPException(409, "Database instance is already being deleted")
    headers = _auth_headers(request)
    i.status, i.error = "deleting", None
    db.commit()
    run_async(provision.teardown, SessionLocal, Instance, i.id, make_api(headers))
    return _out(i)

def _power(iid: str, action: str, request: Request, current: dict, db: Session) -> dict:
    i = _get(db, iid, current, write=True)
    want = {"start": "stopped", "stop": "active"}[action]
    if i.status != want:
        raise HTTPException(409, f"Instance is {i.status}; {action} needs {want}")
    api = make_api(_auth_headers(request))
    try:
        if i.backend == "vm":
            api.call("compute", "POST", f"/servers/{i.server_id}/{action}")
        else:
            api.call("container", "POST", f"/containers/{i.container_id}/{action}")
    except provision.Failure as exc:
        raise HTTPException(502, str(exc)) from None
    i.status = "stopped" if action == "stop" else "active"
    db.commit()
    return _out(i)

@app.post("/instances/{iid}/start")
def start_instance(iid: str, request: Request, current: CurrentUser, db: DB):
    return _power(iid, "start", request, current, db)

@app.post("/instances/{iid}/stop")
def stop_instance(iid: str, request: Request, current: CurrentUser, db: DB):
    return _power(iid, "stop", request, current, db)

@app.post("/internal/db-ready/{iid}")
def db_ready(iid: str, db: DB, x_ready_token: Annotated[str, Header()] = ""):
    """Called by cloud-init in the VM. Only flips a building row to active, with the one-time token."""
    i = db.get(Instance, iid)
    if not i or i.status != "building" or not i.token_hash or not x_ready_token \
            or not hmac.compare_digest(provision.token_hash(x_ready_token), i.token_hash):
        raise HTTPException(403, "Not allowed")
    i.status, i.token_hash = "active", None
    db.commit()
    return {"ok": True}


# ── backup / restore (VM + block only: a crash-consistent copy of the data volume) ──

def _bout(b: Backup) -> dict:
    return {"id": b.id, "name": b.name, "instance_id": b.instance_id, "engine": b.engine, "size_gb": b.size_gb,
            "status": b.status, "error": b.error, "created_at": b.created_at.isoformat() if b.created_at else None}

def _get_backup(db: Session, bid: str, current: dict, write: bool = False) -> Backup:
    b = db.get(Backup, bid)
    if not b or not can_see(current, b.project_id):
        raise HTTPException(404, "Database backup not found")
    if write and not owns(current, b.project_id):
        raise HTTPException(403, "Not allowed")
    return b

class BackupIn(BaseModel):
    name: str | None = None

class RestoreIn(BaseModel):
    name: str
    network_id: str
    image: str
    flavor: str
    allowed_cidr: str | None = None
    password: str | None = None

    @field_validator("password")
    @classmethod
    def _pw(cls, v):
        return check_password(v) if v is not None else v

    @field_validator("allowed_cidr")
    @classmethod
    def _cidr(cls, v):
        return check_cidr(v) if v is not None else v

@app.post("/instances/{iid}/backups", status_code=202)
def create_backup(iid: str, body: BackupIn, request: Request, current: CurrentUser, db: DB):
    i = _get(db, iid, current, write=True)
    if i.backend != "vm" or i.storage != "block":
        raise HTTPException(422, "backups are only supported for a vm with block storage")
    if i.status != "active" or not i.volume_id:
        raise HTTPException(409, f"Instance is {i.status}; a backup needs an active instance")
    api = make_api(_auth_headers(request))
    b = Backup(instance_id=i.id, project_id=i.project_id, name=body.name or f"{i.name}-backup", engine=i.engine,
               size_gb=i.size_gb or 1)
    db.add(b)
    db.commit()
    try:
        b.volume_backup_id = api.call("block", "POST", f"/volumes/{i.volume_id}/backup", {"name": b.name})["id"]
    except provision.Failure as exc:
        db.delete(b)
        db.commit()
        raise HTTPException(502, str(exc)) from None
    db.commit()
    run_async(provision.backup_watch, SessionLocal, Backup, b.id, api)
    return _bout(b)

@app.get("/backups")
def list_backups(current: CurrentUser, db: DB):
    ids = visible_projects(current)
    q = select(Backup).order_by(Backup.created_at)
    if ids is not None:
        q = q.where(Backup.project_id.in_(ids or [_pid(current)]))
    return {"items": [_bout(b) for b in db.scalars(q)]}

@app.get("/backups/{bid}")
def get_backup(bid: str, current: CurrentUser, db: DB):
    return _bout(_get_backup(db, bid, current))

@app.delete("/backups/{bid}", status_code=202)
def delete_backup(bid: str, request: Request, current: CurrentUser, db: DB):
    b = _get_backup(db, bid, current, write=True)
    if bid in RESTORING:
        raise HTTPException(409, "Backup is being restored")
    if b.volume_backup_id:
        try:
            make_api(_auth_headers(request)).call("block", "DELETE", f"/backups/{b.volume_backup_id}", ok=(200, 202, 204, 404))
        except provision.Failure as exc:
            raise HTTPException(502, str(exc)) from None
    db.delete(b)
    db.commit()
    return {"id": bid, "status": "deleting"}

def _restore_job(bid, *args):
    try:
        provision.build(*args)
    finally:
        RESTORING.discard(bid)

@app.post("/backups/{bid}/restore", status_code=201)
def restore_backup(bid: str, body: RestoreIn, request: Request, current: CurrentUser, db: DB):
    b = _get_backup(db, bid, current, write=True)
    if b.status != "available" or not b.volume_backup_id:
        raise HTTPException(409, f"Backup is {b.status}; restore needs an available backup")
    headers = _auth_headers(request)
    password = body.password or gen_password()
    i = Instance(name=body.name, project_id=b.project_id, engine=b.engine, backend="vm", storage="block",
                 network_id=body.network_id, size_gb=b.size_gb, image=body.image, flavor=body.flavor,
                 allowed_cidr=body.allowed_cidr)
    db.add(i)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Database instance name already exists") from None
    spec = {"engine": b.engine, "backend": "vm", "storage": "block", "network_id": body.network_id, "size_gb": b.size_gb,
            "image": body.image, "flavor": body.flavor, "allowed_cidr": body.allowed_cidr, "password": password,
            "public_url": PUBLIC_URL, "backup_volume_id": b.volume_backup_id, "restore": True}
    RESTORING.add(bid)
    run_async(_restore_job, bid, SessionLocal, Instance, i.id, make_api(headers), spec)
    return _out(i, password)
