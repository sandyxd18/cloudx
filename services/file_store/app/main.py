from __future__ import annotations
import os
from shared.config import load_service_config
load_service_config("file-store-api")

"""cloudx file-store API (Manila-like): NFS shares on a tenant network, with per-IP access rules. file-store-worker does the work."""

import logging
import uuid
from datetime import datetime, timedelta
from typing import Annotated

import requests
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator
from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from shared import catalog
from shared.auth_middleware import CurrentUser, InternalAuth
from shared.cors import cors_origins
from shared.domain import can_see, owns, visible_projects
from shared.fs_validate import check_access, check_size
from shared.internal import internal_headers
from shared.request_id_middleware import RequestIDMiddleware
from shared.schema import require_schema

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://cloudx:cloudx@127.0.0.1:5432/cloudx")
HOST = os.getenv("FILE_STORE_HOST", "")
log = logging.getLogger("file-store-api")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)

class Base(DeclarativeBase):
    pass

def new_id() -> str:
    return str(uuid.uuid4())

class ShareNetwork(Base):
    __tablename__ = "fs_share_networks"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String)
    project_id: Mapped[str] = mapped_column(String, index=True)
    network_id: Mapped[str] = mapped_column(String)
    subnet_id: Mapped[str | None] = mapped_column(String, nullable=True)
    port_id: Mapped[str | None] = mapped_column(String, nullable=True)
    port_name: Mapped[str | None] = mapped_column(String, nullable=True)
    ip: Mapped[str | None] = mapped_column(String, nullable=True)
    mac: Mapped[str | None] = mapped_column(String, nullable=True)
    prefix: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gateway: Mapped[str | None] = mapped_column(String, nullable=True)
    switch: Mapped[str | None] = mapped_column(String, nullable=True)
    host: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="creating")  # creating | active | deleting | error
    ready_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # first time it was usable; the reaper never deletes these
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

class Share(Base):
    __tablename__ = "fs_shares"
    __table_args__ = (UniqueConstraint("project_id", "name"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String)
    project_id: Mapped[str] = mapped_column(String, index=True)
    share_network_id: Mapped[str] = mapped_column(ForeignKey("fs_share_networks.id"), index=True)
    size_gb: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String, default="creating")  # creating | available | extending | deleting | error
    ready_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # first time it was usable; the reaper never deletes these
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

class AccessRule(Base):
    __tablename__ = "fs_access_rules"
    __table_args__ = (UniqueConstraint("share_id", "to_cidr"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    share_id: Mapped[str] = mapped_column(ForeignKey("fs_shares.id"), index=True)
    to_cidr: Mapped[str] = mapped_column(String)
    level: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

app = FastAPI(title="cloudx file-store-api", version="1.0.0")
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
    require_schema(engine, "file_store")

@app.get("/health")
def health():
    return {"status": "ok"}

def _pid(current: dict) -> str:
    return current.get("project_id") or current.get("tenant_id") or "default"

def _net(method: str, path: str, **kw) -> dict:
    headers = {**kw.pop("headers", {}), "Content-Type": "application/json", **internal_headers()}
    r = requests.request(method, f"{catalog.url('network').rstrip('/')}{path}", headers=headers, timeout=15, **kw)
    r.raise_for_status()
    return r.json() if r.content else {}

def _release_port(port_id: str | None) -> None:
    if not port_id:
        return
    try:
        _net("DELETE", f"/ports/{port_id}")
    except (requests.RequestException, catalog.CatalogError) as exc:
        log.warning("failed to release port %s: %s", port_id, exc)

def _sn_out(n: ShareNetwork) -> dict:
    return {"id": n.id, "name": n.name, "project_id": n.project_id, "network_id": n.network_id, "subnet_id": n.subnet_id,
            "ip": n.ip, "host": n.host, "status": n.status, "created_at": n.created_at.isoformat()}

def _share_out(db: Session, s: Share) -> dict:
    n = db.get(ShareNetwork, s.share_network_id)
    return {"id": s.id, "name": s.name, "project_id": s.project_id, "share_network_id": s.share_network_id,
            "size_gb": s.size_gb, "status": s.status, "protocol": "NFS",
            "export_location": f"{n.ip}:/{s.id}" if n and n.ip else None, "created_at": s.created_at.isoformat()}

def _get(db: Session, model, ref: str, current: dict, write: bool = False):
    o = db.get(model, ref)
    if not o or not can_see(current, o.project_id):
        raise HTTPException(404, f"{model.__name__} not found")
    if write and not owns(current, o.project_id):
        raise HTTPException(403, "Not allowed")
    return o

def _list(db: Session, model, current: dict) -> list:
    ids = visible_projects(current)
    q = select(model).order_by(model.created_at)
    if ids is not None:
        q = q.where(model.project_id.in_(ids or [_pid(current)]))
    return list(db.scalars(q))

# --- share networks ---

class ShareNetworkIn(BaseModel):
    name: str
    network_id: str
    subnet_id: str | None = None

@app.post("/share-networks", status_code=201)
def create_share_network(body: ShareNetworkIn, current: CurrentUser, db: DB):
    payload = {"name": f"fs-{new_id()}", "network": body.network_id, "device_owner": "cloudx:fileshare"}
    if body.subnet_id:
        payload["fixed_ips"] = [{"subnet_id": body.subnet_id}]
    try:
        port = _net("POST", "/ports", json=payload)
        fixed = port["fixed_ips"][0]
        subnet = _net("GET", f"/subnets/{fixed['subnet_id']}")
        net = _net("GET", f"/networks/{port['network_id']}")
    except (requests.RequestException, catalog.CatalogError, KeyError, IndexError, ValueError) as exc:
        log.error("share network port allocation failed: %s", exc)
        raise HTTPException(502, f"Port allocation failed: {exc}") from None
    n = ShareNetwork(name=body.name, project_id=_pid(current), network_id=body.network_id, subnet_id=fixed["subnet_id"],
                     port_id=port["id"], port_name=port["name"], ip=fixed["ip_address"], mac=port["mac_address"],
                     prefix=int(subnet["cidr"].split("/")[1]), gateway=subnet.get("gateway_ip"), switch=net["name"], host=HOST)
    db.add(n)
    db.commit()
    return _sn_out(n)

@app.get("/share-networks")
def list_share_networks(current: CurrentUser, db: DB):
    return {"items": [_sn_out(n) for n in _list(db, ShareNetwork, current)]}

@app.get("/share-networks/{nid}")
def get_share_network(nid: str, current: CurrentUser, db: DB):
    return _sn_out(_get(db, ShareNetwork, nid, current))

@app.delete("/share-networks/{nid}", status_code=202)
def delete_share_network(nid: str, current: CurrentUser, db: DB):
    n = _get(db, ShareNetwork, nid, current, write=True)
    if db.scalar(select(func.count()).select_from(Share).where(Share.share_network_id == nid)):
        raise HTTPException(409, "Share network still has shares")
    n.status = "deleting"
    db.commit()
    return _sn_out(n)

# --- shares ---

class ShareIn(BaseModel):
    name: str
    size_gb: int
    share_network_id: str

    @field_validator("size_gb")
    @classmethod
    def _size(cls, v):
        return check_size(v)

class ExtendIn(BaseModel):
    size_gb: int

    @field_validator("size_gb")
    @classmethod
    def _size(cls, v):
        return check_size(v)

@app.post("/shares", status_code=201)
def create_share(body: ShareIn, current: CurrentUser, db: DB):
    n = _get(db, ShareNetwork, body.share_network_id, current, write=True)
    if n.status != "active":
        raise HTTPException(409, f"Share network is {n.status}, not active")
    s = Share(name=body.name, project_id=_pid(current), share_network_id=n.id, size_gb=body.size_gb)
    db.add(s)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Share name already exists") from None
    return _share_out(db, s)

@app.get("/shares")
def list_shares(current: CurrentUser, db: DB):
    return {"items": [_share_out(db, s) for s in _list(db, Share, current)]}

@app.get("/shares/{sid}")
def get_share(sid: str, current: CurrentUser, db: DB):
    return _share_out(db, _get(db, Share, sid, current))

@app.post("/shares/{sid}/extend")
def extend_share(sid: str, body: ExtendIn, current: CurrentUser, db: DB):
    s = _get(db, Share, sid, current, write=True)
    if s.status != "available":
        raise HTTPException(409, f"Share is {s.status}, not available")
    if body.size_gb <= s.size_gb:
        raise HTTPException(409, "New size must be larger than the current size")
    s.size_gb, s.status = body.size_gb, "extending"
    db.commit()
    return _share_out(db, s)

@app.delete("/shares/{sid}", status_code=202)
def delete_share(sid: str, current: CurrentUser, db: DB):
    s = _get(db, Share, sid, current, write=True)
    if s.status == "deleting":
        raise HTTPException(409, "Share is already being deleted")
    s.status = "deleting"
    db.commit()
    return _share_out(db, s)

# --- access rules ---

class AccessIn(BaseModel):
    to: str
    level: str = "rw"

def _rule_out(r: AccessRule) -> dict:
    return {"id": r.id, "share_id": r.share_id, "to": r.to_cidr, "level": r.level}

@app.post("/shares/{sid}/access", status_code=201)
def add_access(sid: str, body: AccessIn, current: CurrentUser, db: DB):
    s = _get(db, Share, sid, current, write=True)
    try:
        cidr, level = check_access(body.to, body.level)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    r = AccessRule(share_id=s.id, to_cidr=cidr, level=level)
    db.add(r)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Rule for this address already exists") from None
    return _rule_out(r)

@app.get("/shares/{sid}/access")
def list_access(sid: str, current: CurrentUser, db: DB):
    s = _get(db, Share, sid, current)
    return {"items": [_rule_out(r) for r in db.scalars(select(AccessRule).where(AccessRule.share_id == s.id).order_by(AccessRule.created_at))]}

@app.delete("/shares/{sid}/access/{rid}", status_code=204)
def delete_access(sid: str, rid: str, current: CurrentUser, db: DB):
    s = _get(db, Share, sid, current, write=True)
    r = db.get(AccessRule, rid)
    if not r or r.share_id != s.id:
        raise HTTPException(404, "Rule not found")
    db.delete(r)
    db.commit()

# --- worker ---

@app.get("/internal/fs-agent/{host}", dependencies=[InternalAuth])
def agent_state(host: str, db: DB):
    nets = list(db.scalars(select(ShareNetwork).where(ShareNetwork.host == host)))
    ids = [n.id for n in nets]
    shares = list(db.scalars(select(Share).where(Share.share_network_id.in_(ids)))) if ids else []
    rules: dict[str, list] = {}
    for r in db.scalars(select(AccessRule).where(AccessRule.share_id.in_([s.id for s in shares]))) if shares else []:
        rules.setdefault(r.share_id, []).append({"to": r.to_cidr, "level": r.level})
    return {
        "share_networks": [{"id": n.id, "port_name": n.port_name, "mac": n.mac, "ip": n.ip, "prefix": n.prefix,
                            "gateway": n.gateway, "status": n.status} for n in nets],
        "shares": [{"id": s.id, "share_network_id": s.share_network_id, "size_gb": s.size_gb, "status": s.status,
                    "rules": rules.get(s.id, [])} for s in shares],
    }

class ReportIn(BaseModel):
    share_networks: dict[str, str] = {}
    shares: dict[str, str] = {}

@app.post("/internal/fs-agent/{host}/report", dependencies=[InternalAuth])
def agent_report(host: str, body: ReportIn, db: DB):
    for sid, st in body.shares.items():
        s = db.get(Share, sid)
        if not s:
            continue
        if st == "deleted":
            db.query(AccessRule).filter_by(share_id=sid).delete()
            db.delete(s)
        elif s.status != "deleting":  # a delete request must not be overwritten by a late report
            s.status = st
            if st == "available" and not s.ready_at:
                s.ready_at = datetime.utcnow()
    for nid, st in body.share_networks.items():
        n = db.get(ShareNetwork, nid)
        if not n:
            continue
        if st == "deleted":
            _release_port(n.port_id)
            db.delete(n)
        elif n.status != "deleting":
            n.status = st
            if st == "active" and not n.ready_at:
                n.ready_at = datetime.utcnow()
    db.commit()
    return {"ok": True}

@app.get("/internal/stale-errors", dependencies=[InternalAuth])
def stale_errors(hours: float, db: DB):
    """Shares, then share networks, that failed before ever becoming ready and are older than `hours` (the reaper deletes these, in this order)."""
    cut = datetime.utcnow() - timedelta(hours=hours)
    items = [{"id": r.id, "kind": k}
             for k, M in (("share", Share), ("share-network", ShareNetwork))
             for r in db.scalars(select(M).where(M.status == "error", M.ready_at.is_(None), M.created_at < cut))]
    return {"items": items}
