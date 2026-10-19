from __future__ import annotations
import os
from shared.config import load_service_config
load_service_config("container-api")

"""cloudx container API (Zun-like): single containers on compute nodes with a tenant OVN port. container-worker runs them."""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated

import requests
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator
from sqlalchemy import JSON, DateTime, Float, Integer, String, Text, UniqueConstraint, create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from shared.auth_middleware import CurrentUser, InternalAuth
from shared.cors import cors_origins
from shared.ct_validate import check_data_path, check_env, check_image, check_limits
from shared.domain import can_see, owns, visible_projects
from shared.internal import internal_headers
from shared.request_id_middleware import RequestIDMiddleware
from shared.schema import require_schema

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://cloudx:cloudx@127.0.0.1:5432/cloudx")
NETWORKING_API_URL = os.getenv("NETWORKING_API_URL", "http://127.0.0.1:8001").rstrip("/")
HOST_TTL = timedelta(seconds=30)
LOG_CAP = 16 * 1024
log = logging.getLogger("container-api")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)

class Base(DeclarativeBase):
    pass

def new_id() -> str:
    return str(uuid.uuid4())

def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)

class Container(Base):
    __tablename__ = "ct_containers"
    __table_args__ = (UniqueConstraint("project_id", "name"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String)
    project_id: Mapped[str] = mapped_column(String, index=True)
    image: Mapped[str] = mapped_column(String)
    command: Mapped[list] = mapped_column(JSON, default=list)
    env: Mapped[dict] = mapped_column(JSON, default=dict)
    cpu: Mapped[float] = mapped_column(Float)
    memory_mb: Mapped[int] = mapped_column(Integer)
    network_id: Mapped[str] = mapped_column(String)
    subnet_id: Mapped[str | None] = mapped_column(String, nullable=True)
    port_id: Mapped[str | None] = mapped_column(String, nullable=True)
    port_name: Mapped[str | None] = mapped_column(String, nullable=True)
    ip: Mapped[str | None] = mapped_column(String, nullable=True)
    mac: Mapped[str | None] = mapped_column(String, nullable=True)
    prefix: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gateway: Mapped[str | None] = mapped_column(String, nullable=True)
    dns: Mapped[list] = mapped_column(JSON, default=list)
    data_path: Mapped[str | None] = mapped_column(String, nullable=True)
    host: Mapped[str] = mapped_column(String, index=True)
    status: Mapped[str] = mapped_column(String, default="creating")  # creating | running | stopped | error | deleting
    want: Mapped[str] = mapped_column(String, default="running")  # running | stopped | deleting
    logs: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

class Host(Base):
    __tablename__ = "ct_hosts"
    host: Mapped[str] = mapped_column(String, primary_key=True)
    seen_at: Mapped[datetime] = mapped_column(DateTime)

app = FastAPI(title="cloudx container-api", version="1.0.0")
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

@app.on_event("startup")
def startup():
    require_schema(engine, "container")

@app.get("/health")
def health():
    return {"status": "ok"}

def _pid(current: dict) -> str:
    return current.get("project_id") or current.get("tenant_id") or "default"

def _net(method: str, path: str, **kw) -> dict:
    headers = {**kw.pop("headers", {}), "Content-Type": "application/json", **internal_headers()}
    r = requests.request(method, f"{NETWORKING_API_URL}{path}", headers=headers, timeout=15, **kw)
    r.raise_for_status()
    return r.json() if r.content else {}

def _release_port(port_id: str | None) -> None:
    if not port_id:
        return
    try:
        _net("DELETE", f"/ports/{port_id}")
    except requests.RequestException as exc:
        log.warning("failed to release port %s: %s", port_id, exc)

def _out(c: Container) -> dict:
    return {"id": c.id, "name": c.name, "project_id": c.project_id, "image": c.image, "command": c.command, "env": c.env,
            "cpu": c.cpu, "memory_mb": c.memory_mb, "network_id": c.network_id, "ip": c.ip, "host": c.host, "data_path": c.data_path,
            "status": c.status, "error": c.error, "created_at": c.created_at.isoformat()}

def _get(db: Session, ref: str, current: dict, write: bool = False) -> Container:
    c = db.get(Container, ref)
    if not c or not can_see(current, c.project_id):
        raise HTTPException(404, "Container not found")
    if write and not owns(current, c.project_id):
        raise HTTPException(403, "Not allowed")
    return c

def _pick_host(db: Session) -> str:
    live = [h.host for h in db.scalars(select(Host).where(Host.seen_at > now() - HOST_TTL))]
    if not live:
        raise HTTPException(503, "No container host is online")
    load = {h: 0 for h in live}
    for host, n in db.execute(select(Container.host, func.count()).where(Container.want != "deleting").group_by(Container.host)):
        if host in load:
            load[host] = n
    return min(sorted(load), key=lambda h: load[h])

class ContainerIn(BaseModel):
    name: str
    image: str
    network_id: str
    subnet_id: str | None = None
    command: list[str] = []
    env: dict[str, str] = {}
    cpu: float = 0.5
    memory_mb: int = 128
    security_group_ids: list[str] = []
    data_path: str | None = None

    @field_validator("data_path")
    @classmethod
    def _data_path(cls, v):
        return check_data_path(v) if v is not None else v

    @field_validator("image")
    @classmethod
    def _image(cls, v):
        return check_image(v)

    @field_validator("env")
    @classmethod
    def _env(cls, v):
        return check_env(v)

    @field_validator("command")
    @classmethod
    def _command(cls, v):
        if len(v) > 50 or any(len(a) > 1024 or "\0" in a for a in v):
            raise ValueError("command: at most 50 arguments of 1024 characters")
        return v

    @field_validator("memory_mb")
    @classmethod
    def _mem(cls, v):
        return v

def _check(body: ContainerIn) -> None:
    try:
        check_limits(body.cpu, body.memory_mb)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None

@app.post("/containers", status_code=201)
def create_container(body: ContainerIn, current: CurrentUser, db: DB):
    _check(body)
    host = _pick_host(db)
    payload = {"name": f"ct-{new_id()}", "network": body.network_id, "device_owner": "cloudx:container"}
    if body.subnet_id:
        payload["fixed_ips"] = [{"subnet_id": body.subnet_id}]
    if body.security_group_ids:
        payload["security_group_ids"] = body.security_group_ids
    try:
        port = _net("POST", "/ports", json=payload)
        fixed = port["fixed_ips"][0]
        subnet = _net("GET", f"/subnets/{fixed['subnet_id']}")
    except (requests.RequestException, KeyError, IndexError, ValueError) as exc:
        log.error("container port allocation failed: %s", exc)
        raise HTTPException(502, f"Port allocation failed: {exc}") from None
    c = Container(name=body.name, project_id=_pid(current), image=body.image, command=body.command, env=body.env,
                  cpu=body.cpu, memory_mb=body.memory_mb, network_id=body.network_id, subnet_id=fixed["subnet_id"],
                  port_id=port["id"], port_name=port["name"], ip=fixed["ip_address"], mac=port["mac_address"],
                  prefix=int(subnet["cidr"].split("/")[1]), gateway=subnet.get("gateway_ip"),
                  dns=subnet.get("dns_nameservers") or [], data_path=body.data_path, host=host)
    db.add(c)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        _release_port(port["id"])
        raise HTTPException(409, "Container name already exists") from None
    return _out(c)

@app.get("/containers")
def list_containers(current: CurrentUser, db: DB):
    ids = visible_projects(current)
    q = select(Container).order_by(Container.created_at)
    if ids is not None:
        q = q.where(Container.project_id.in_(ids or [_pid(current)]))
    return {"items": [_out(c) for c in db.scalars(q)]}

@app.get("/containers/{cid}")
def get_container(cid: str, current: CurrentUser, db: DB):
    return _out(_get(db, cid, current))

@app.get("/containers/{cid}/logs")
def container_logs(cid: str, current: CurrentUser, db: DB):
    return {"logs": _get(db, cid, current).logs or ""}

def _set_want(cid: str, want: str, status: str, current: dict, db: Session) -> dict:
    c = _get(db, cid, current, write=True)
    if c.want == "deleting":
        raise HTTPException(409, "Container is being deleted")
    c.want, c.status = want, status
    db.commit()
    return _out(c)

@app.post("/containers/{cid}/start")
def start_container(cid: str, current: CurrentUser, db: DB):
    return _set_want(cid, "running", "creating", current, db)

@app.post("/containers/{cid}/stop")
def stop_container(cid: str, current: CurrentUser, db: DB):
    return _set_want(cid, "stopped", "stopped", current, db)

@app.post("/containers/{cid}/restart")
def restart_container(cid: str, current: CurrentUser, db: DB):
    c = _get(db, cid, current, write=True)
    if c.want == "deleting":
        raise HTTPException(409, "Container is being deleted")
    c.want, c.status = "restart", "creating"
    db.commit()
    return _out(c)

@app.delete("/containers/{cid}", status_code=202)
def delete_container(cid: str, current: CurrentUser, db: DB):
    c = _get(db, cid, current, write=True)
    c.want, c.status = "deleting", "deleting"
    db.commit()
    return _out(c)

@app.get("/internal/ct-agent/{host}", dependencies=[InternalAuth])
def agent_state(host: str, db: DB):
    h = db.get(Host, host)
    if h:
        h.seen_at = now()
    else:
        db.add(Host(host=host, seen_at=now()))
    db.commit()
    rows = db.scalars(select(Container).where(Container.host == host))
    return {"containers": [{"id": c.id, "image": c.image, "command": c.command, "env": c.env, "cpu": c.cpu,
                            "memory_mb": c.memory_mb, "port_name": c.port_name, "mac": c.mac, "ip": c.ip,
                            "prefix": c.prefix, "gateway": c.gateway, "dns": c.dns, "data_path": c.data_path, "want": c.want} for c in rows]}

class ItemIn(BaseModel):
    state: str
    error: str | None = None
    logs: str | None = None

class ReportIn(BaseModel):
    containers: dict[str, ItemIn] = {}

@app.post("/internal/ct-agent/{host}/report", dependencies=[InternalAuth])
def agent_report(host: str, body: ReportIn, db: DB):
    for cid, it in body.containers.items():
        c = db.get(Container, cid)
        if not c or c.host != host:
            continue
        if it.state == "deleted":
            _release_port(c.port_id)
            db.delete(c)
            continue
        if it.logs is not None:
            c.logs = it.logs[-LOG_CAP:]
        if c.want == "deleting":  # a delete request must not be overwritten by a late report
            continue
        if it.state in ("running", "stopped", "error"):
            c.status, c.error = it.state, it.error
            if it.state == "running" and c.want == "restart":  # the worker restarts before it reports
                c.want = "running"
    db.commit()
    return {"ok": True}
