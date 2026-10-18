from __future__ import annotations
import os
from shared.config import load_service_config
load_service_config("dns-api")

"""cloudx DNS API (Designate-like): zones, recordsets, secondaries and the platform's automatic names. BIND is driven by dns-worker."""

import ipaddress
import logging
import time
import uuid
from datetime import datetime
from typing import Annotated

import requests
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint, create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from shared.auth_middleware import CurrentUser, InternalAuth
from shared.cors import cors_origins
from shared.domain import can_see, check_domain_caps, owns, visible_projects
from shared.dns_validate import check_cname_rules, check_record, normalize_name, normalize_zone_name
from shared.internal import internal_headers
from shared.request_id_middleware import RequestIDMiddleware
from shared.schema import require_schema

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://cloudx:cloudx@127.0.0.1:5432/cloudx")
AUTH_API_URL = os.getenv("AUTH_API_URL", "http://auth-api:8000").rstrip("/")
BASE = os.getenv("DNS_PLATFORM_DOMAIN", "cloud.lab").strip().lower().rstrip(".")
PUBLIC_ADDRESS = os.getenv("DNS_PUBLIC_ADDRESS", "172.20.0.120")
ADMIN_EMAIL = os.getenv("DNS_ADMIN_EMAIL", f"hostmaster@{BASE}")
log = logging.getLogger("dns-api")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    pass


def new_id() -> str:
    return str(uuid.uuid4())


class Zone(Base):
    __tablename__ = "dns_zones"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String, unique=True, index=True)
    project_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String, default="user")  # user | platform
    scope: Mapped[str] = mapped_column(String, default="public")  # public | internal
    email: Mapped[str] = mapped_column(String, default="")
    ttl: Mapped[int] = mapped_column(Integer, default=300)
    serial: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String, default="PENDING")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RecordSet(Base):
    __tablename__ = "dns_recordsets"
    __table_args__ = (UniqueConstraint("zone_id", "name", "type"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    zone_id: Mapped[str] = mapped_column(ForeignKey("dns_zones.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    name: Mapped[str] = mapped_column(String)
    type: Mapped[str] = mapped_column(String)
    ttl: Mapped[int | None] = mapped_column(Integer, nullable=True)
    records: Mapped[list] = mapped_column(JSON)
    managed_by: Mapped[str | None] = mapped_column(String, nullable=True, index=True)  # "<resource_type>:<id>"


class Secondary(Base):
    __tablename__ = "dns_secondaries"
    __table_args__ = (UniqueConstraint("zone_id", "address"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    zone_id: Mapped[str] = mapped_column(ForeignKey("dns_zones.id", ondelete="CASCADE"), index=True)
    address: Mapped[str] = mapped_column(String)


app = FastAPI(title="cloudx dns-api", version="1.0.0")
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


def seed(db: Session) -> None:
    """The platform zones. Idempotent."""
    for sub, scope in (("", "public"), ("compute.", "public"), ("elb.", "public"), ("s3.", "public"), ("internal.", "internal")):
        if not db.scalar(select(Zone).where(Zone.name == sub + BASE)):
            db.add(Zone(name=sub + BASE, kind="platform", scope=scope, email=ADMIN_EMAIL, serial=_tick()))
    db.flush()
    base = db.scalar(select(Zone).where(Zone.name == BASE))
    ns = db.scalar(select(RecordSet).where(RecordSet.zone_id == base.id, RecordSet.name == "ns1", RecordSet.type == "A"))
    if not ns:
        db.add(RecordSet(zone_id=base.id, name="ns1", type="A", records=[PUBLIC_ADDRESS], managed_by="platform:ns1"))
    elif ns.records != [PUBLIC_ADDRESS]:
        ns.records = [PUBLIC_ADDRESS]
        _bump(base)
    db.commit()


@app.on_event("startup")
def startup():
    require_schema(engine, "dns")
    with SessionLocal() as db:
        seed(db)


@app.get("/health")
def health():
    return {"status": "ok"}


def _tick() -> int:
    return int(time.time())


def _bump(z: Zone) -> None:
    z.serial = max(z.serial + 1, _tick())
    z.status = "PENDING"


def _pid(current: dict) -> str:
    return current.get("project_id") or current.get("tenant_id") or "default"


def _zone_out(z: Zone) -> dict:
    return {"id": z.id, "name": z.name, "project_id": z.project_id, "kind": z.kind, "scope": z.scope, "email": z.email,
            "ttl": z.ttl, "serial": z.serial, "status": z.status, "created_at": z.created_at.isoformat()}


def _rs_out(r: RecordSet, z: Zone) -> dict:
    return {"id": r.id, "zone_id": r.zone_id, "name": r.name, "type": r.type, "ttl": r.ttl, "records": r.records,
            "managed_by": r.managed_by, "status": z.status, "project_id": r.project_id}


def _zone(db: Session, ref: str, current: dict, write: bool = False) -> Zone:
    z = db.get(Zone, ref) or db.scalar(select(Zone).where(Zone.name == ref.lower().rstrip(".")))
    if not z or (z.kind == "user" and not can_see(current, z.project_id)):
        raise HTTPException(404, "Zone not found")
    if write and (z.kind == "platform" or not owns(current, z.project_id)):
        raise HTTPException(403, "Zone is read-only")
    return z


def _check_quota(db: Session, project_id: str, key: str, model, extra=()) -> None:
    try:
        r = requests.get(f"{AUTH_API_URL}/internal/projects/{project_id}/quota", headers=internal_headers(), timeout=5)
        r.raise_for_status()
        body = r.json()
    except (requests.RequestException, ValueError) as exc:
        log.warning("quota check skipped (auth-api): %s", exc)  # ponytail: fails open like the other services
        return

    def used(ids):
        return db.scalar(select(func.count()).select_from(model).where(model.project_id.in_(ids), *extra))
    check_domain_caps(db, body.get("domain") or {}, {f"max_{key}": (used, 1)})
    limit = body.get(f"max_{key}")
    if limit is not None and used([project_id]) >= limit:
        raise HTTPException(413, f"Quota exceeded: max_{key} ({limit}) reached")


class ZoneIn(BaseModel):
    name: str
    email: str | None = None
    ttl: int = Field(default=300, ge=60, le=86400)
    scope: str = "public"

    @field_validator("name")
    @classmethod
    def _n(cls, v):
        return normalize_zone_name(v)

    @field_validator("scope")
    @classmethod
    def _s(cls, v):
        if v not in ("public", "internal"):
            raise ValueError("scope must be public or internal")
        return v

    @field_validator("email")
    @classmethod
    def _e(cls, v):
        if v is not None and (v.count("@") != 1 or any(c in v for c in " \t\r\n;\"\\")):
            raise ValueError("invalid email")
        return v


class ZoneUpdate(BaseModel):
    email: str | None = None
    ttl: int | None = Field(default=None, ge=60, le=86400)


def _overlaps_platform(name: str) -> bool:
    return name == BASE or name.endswith("." + BASE)


@app.post("/zones", status_code=201)
def create_zone(body: ZoneIn, current: CurrentUser, db: DB):
    if _overlaps_platform(body.name):
        raise HTTPException(400, f"Names under {BASE} are reserved for the platform")
    pid = _pid(current)
    _check_quota(db, pid, "zones", Zone, (Zone.kind == "user",))
    z = Zone(name=body.name, project_id=pid, scope=body.scope, ttl=body.ttl, serial=_tick(),
             email=body.email or f"hostmaster@{body.name}")
    db.add(z)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Zone already exists") from None
    return _zone_out(z)


@app.get("/zones")
def list_zones(current: CurrentUser, db: DB):
    ids = visible_projects(current)
    q = select(Zone).order_by(Zone.name)
    if ids is not None:
        q = q.where((Zone.kind == "platform") | Zone.project_id.in_(ids or [_pid(current)]))
    return {"zones": [_zone_out(z) for z in db.scalars(q)]}


@app.get("/zones/{ref}")
def get_zone(ref: str, current: CurrentUser, db: DB):
    return _zone_out(_zone(db, ref, current))


@app.patch("/zones/{ref}")
def update_zone(ref: str, body: ZoneUpdate, current: CurrentUser, db: DB):
    z = _zone(db, ref, current, write=True)
    if body.email is not None:
        ZoneIn(name=z.name, email=body.email)
        z.email = body.email
    if body.ttl is not None:
        z.ttl = body.ttl
    _bump(z)
    db.commit()
    return _zone_out(z)


@app.delete("/zones/{ref}", status_code=204)
def delete_zone(ref: str, current: CurrentUser, db: DB):
    z = _zone(db, ref, current, write=True)
    db.query(RecordSet).filter_by(zone_id=z.id).delete()
    db.query(Secondary).filter_by(zone_id=z.id).delete()
    db.delete(z)
    db.commit()


class RecordSetIn(BaseModel):
    name: str
    type: str
    records: list[str]
    ttl: int | None = Field(default=None, ge=60, le=86400)


class RecordSetUpdate(BaseModel):
    records: list[str]
    ttl: int | None = Field(default=None, ge=60, le=86400)


def _types_at(db: Session, z: Zone, skip: str | None = None) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for r in db.scalars(select(RecordSet).where(RecordSet.zone_id == z.id)):
        if r.id != skip:
            out.setdefault(r.name, set()).add(r.type)
    return out


def _valid(db: Session, z: Zone, name: str, rtype: str, records: list[str], skip: str | None = None) -> tuple[str, list[str]]:
    try:
        name, rtype = normalize_name(name), rtype.upper()
        if rtype == "NS" and name == "@":
            raise ValueError("apex NS records are managed by the platform")
        data = check_record(rtype, name, records)
        check_cname_rules(_types_at(db, z, skip), name, rtype, name == "@")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    return name, data


@app.post("/zones/{ref}/recordsets", status_code=201)
def create_recordset(ref: str, body: RecordSetIn, current: CurrentUser, db: DB):
    z = _zone(db, ref, current, write=True)
    name, data = _valid(db, z, body.name, body.type, body.records)
    rtype = body.type.upper()
    if db.scalar(select(RecordSet).where(RecordSet.zone_id == z.id, RecordSet.name == name, RecordSet.type == rtype)):
        raise HTTPException(409, "Recordset already exists")
    _check_quota(db, z.project_id, "recordsets", RecordSet, (RecordSet.managed_by.is_(None),))
    r = RecordSet(zone_id=z.id, project_id=z.project_id, name=name, type=rtype, records=data, ttl=body.ttl)
    db.add(r)
    _bump(z)
    db.commit()
    return _rs_out(r, z)


@app.get("/zones/{ref}/recordsets")
def list_recordsets(ref: str, current: CurrentUser, db: DB):
    z = _zone(db, ref, current)
    rows = db.scalars(select(RecordSet).where(RecordSet.zone_id == z.id).order_by(RecordSet.name, RecordSet.type))
    return {"recordsets": [_rs_out(r, z) for r in rows]}


def _rs(db: Session, z: Zone, rid: str) -> RecordSet:
    r = db.get(RecordSet, rid)
    if not r or r.zone_id != z.id:
        raise HTTPException(404, "Recordset not found")
    if r.managed_by:
        raise HTTPException(409, f"Recordset is managed by {r.managed_by}")
    return r


@app.put("/zones/{ref}/recordsets/{rid}")
def update_recordset(ref: str, rid: str, body: RecordSetUpdate, current: CurrentUser, db: DB):
    z = _zone(db, ref, current, write=True)
    r = _rs(db, z, rid)
    _, r.records = _valid(db, z, r.name, r.type, body.records, skip=r.id)
    if body.ttl is not None:
        r.ttl = body.ttl
    _bump(z)
    db.commit()
    return _rs_out(r, z)


@app.delete("/zones/{ref}/recordsets/{rid}", status_code=204)
def delete_recordset(ref: str, rid: str, current: CurrentUser, db: DB):
    z = _zone(db, ref, current, write=True)
    db.delete(_rs(db, z, rid))
    _bump(z)
    db.commit()


class SecondaryIn(BaseModel):
    address: str

    @field_validator("address")
    @classmethod
    def _a(cls, v):
        ip = ipaddress.ip_address(v.strip())
        if ip.is_loopback or ip.is_unspecified or ip.is_multicast:
            raise ValueError("address not allowed")
        return str(ip)


@app.post("/zones/{ref}/secondaries", status_code=201)
def add_secondary(ref: str, body: SecondaryIn, current: CurrentUser, db: DB):
    z = _zone(db, ref, current, write=True)
    if z.scope != "public":
        raise HTTPException(400, "Internal zones cannot have secondaries")
    if db.scalar(select(Secondary).where(Secondary.zone_id == z.id, Secondary.address == body.address)):
        raise HTTPException(409, "Secondary already registered")
    s = Secondary(zone_id=z.id, address=body.address)
    db.add(s)
    _bump(z)
    db.commit()
    return {"id": s.id, "zone_id": z.id, "address": s.address}


@app.get("/zones/{ref}/secondaries")
def list_secondaries(ref: str, current: CurrentUser, db: DB):
    z = _zone(db, ref, current)
    return {"secondaries": [{"id": s.id, "zone_id": z.id, "address": s.address}
                            for s in db.scalars(select(Secondary).where(Secondary.zone_id == z.id))]}


@app.delete("/zones/{ref}/secondaries/{sid}", status_code=204)
def delete_secondary(ref: str, sid: str, current: CurrentUser, db: DB):
    z = _zone(db, ref, current, write=True)
    s = db.get(Secondary, sid)
    if not s or s.zone_id != z.id:
        raise HTTPException(404, "Secondary not found")
    db.delete(s)
    _bump(z)
    db.commit()


# ── internal: automatic names and the worker ───────────────────────────────────

class RegisterIn(BaseModel):
    owner: str
    project_id: str | None = None
    fqdn: str
    type: str = "A"
    records: list[str]
    scope: str = "public"


@app.post("/internal/records", dependencies=[InternalAuth])
def register_record(body: RegisterIn, db: DB):
    if body.type.upper() not in ("A", "AAAA", "CNAME") or ":" not in body.owner:
        raise HTTPException(400, "type must be A, AAAA or CNAME and owner '<type>:<id>'")
    try:
        fqdn = normalize_zone_name(body.fqdn)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    zones = db.scalars(select(Zone).where(Zone.kind == "platform")).all()
    z = max((x for x in zones if fqdn.endswith("." + x.name)), key=lambda x: len(x.name), default=None)
    if not z or z.scope != body.scope:
        raise HTTPException(400, "fqdn is not inside a platform zone of that scope")
    name, data = _valid(db, z, fqdn[: -len(z.name) - 1], body.type, body.records, skip=_owned_id(db, body.owner))
    old = db.scalar(select(RecordSet).where(RecordSet.managed_by == body.owner))
    clash = db.scalar(select(RecordSet).where(RecordSet.zone_id == z.id, RecordSet.name == name, RecordSet.type == body.type.upper()))
    if clash and clash is not old:
        raise HTTPException(409, "name already taken")
    if old and (old.zone_id, old.name, old.type, old.records) == (z.id, name, body.type.upper(), data):
        return {"zone": z.name, "name": name}  # unchanged: do not bump the serial
    if old:
        z_old = db.get(Zone, old.zone_id)
        db.delete(old)
        db.flush()
        _bump(z_old)
    db.add(RecordSet(zone_id=z.id, project_id=body.project_id, name=name, type=body.type.upper(), records=data, managed_by=body.owner))
    _bump(z)
    db.commit()
    return {"zone": z.name, "name": name}


def _owned_id(db: Session, owner: str) -> str | None:
    r = db.scalar(select(RecordSet).where(RecordSet.managed_by == owner))
    return r.id if r else None


@app.delete("/internal/records", status_code=204, dependencies=[InternalAuth])
def unregister_record(owner: str, db: DB):
    r = db.scalar(select(RecordSet).where(RecordSet.managed_by == owner))
    if r:
        z = db.get(Zone, r.zone_id)
        db.delete(r)
        _bump(z)
        db.commit()


@app.get("/internal/records", dependencies=[InternalAuth])
def list_managed(db: DB):
    rows = db.scalars(select(RecordSet).where(RecordSet.managed_by.isnot(None), ~RecordSet.managed_by.like("platform:%"))).all()
    zones = {z.id: z.name for z in db.scalars(select(Zone))}
    return {"owners": [r.managed_by for r in rows],
            "records": {r.managed_by: {"fqdn": f"{r.name}.{zones[r.zone_id]}", "type": r.type, "records": r.records} for r in rows}}


@app.get("/internal/state", dependencies=[InternalAuth])
def state(db: DB):
    out = []
    for z in db.scalars(select(Zone).order_by(Zone.name)):
        rs = db.scalars(select(RecordSet).where(RecordSet.zone_id == z.id).order_by(RecordSet.name, RecordSet.type))
        sec = db.scalars(select(Secondary).where(Secondary.zone_id == z.id))
        out.append({**_zone_out(z), "recordsets": [{"name": r.name, "type": r.type, "ttl": r.ttl, "records": r.records} for r in rs],
                    "secondaries": [s.address for s in sec]})
    return {"base": BASE, "hostmaster_default": ADMIN_EMAIL, "zones": out}


class StatusIn(BaseModel):
    serial: int
    status: str = "ACTIVE"


@app.post("/internal/zones/{zid}/status", dependencies=[InternalAuth])
def zone_status(zid: str, body: StatusIn, db: DB):
    z = db.get(Zone, zid)
    if z and z.serial == body.serial:  # a newer change landed meanwhile: stay PENDING
        z.status = body.status
        db.commit()
    return {"ok": True}
