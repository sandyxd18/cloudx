from __future__ import annotations
import os
from shared.config import load_service_config
load_service_config("reservation-api")

"""cloudx reservation API (Blazar-like): time-boxed leases of flavor capacity, admitted against Placement's VCPU / RAM.

A lease is a capacity ledger entry. It is not enforced against compute (see docs/reservation.md)."""

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated

import requests
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import DateTime, Integer, String, UniqueConstraint, create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from shared.auth_middleware import CurrentUser
from shared.cors import cors_origins
from shared.domain import owns, visible_projects
from shared.request_id_middleware import RequestIDMiddleware
from shared.schema import require_schema

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://cloudx:cloudx@127.0.0.1:5432/cloudx")
COMPUTE_API_URL = os.getenv("COMPUTE_API_URL", "http://127.0.0.1:8003").rstrip("/")
PLACEMENT_API_URL = os.getenv("PLACEMENT_API_URL", "http://127.0.0.1:8006").rstrip("/")
MAX_WINDOW = timedelta(days=30)
NAME_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}\Z")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)

class Base(DeclarativeBase):
    pass

def new_id() -> str:
    return str(uuid.uuid4())

def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)

class Lease(Base):
    __tablename__ = "rsv_leases"
    __table_args__ = (UniqueConstraint("project_id", "name"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(String, index=True)
    name: Mapped[str] = mapped_column(String)
    start_at: Mapped[datetime] = mapped_column(DateTime)
    end_at: Mapped[datetime] = mapped_column(DateTime)
    flavor: Mapped[str] = mapped_column(String)
    amount: Mapped[int] = mapped_column(Integer)
    vcpus: Mapped[int] = mapped_column(Integer)  # flavor x amount, frozen at creation
    ram_mb: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

def peak(leases, start: datetime, end: datetime) -> tuple[int, int]:
    """Highest (vcpus, ram_mb) in use at any moment of [start, end) by leases given as (start, end, vcpus, ram_mb)."""
    live = [x for x in leases if x[0] < end and x[1] > start]
    best = (0, 0)
    for t in {start, *(x[0] for x in live if x[0] > start)}:  # usage only rises when a lease starts
        v = r = 0
        for s, e, lv, lr in live:
            if s <= t < e:
                v, r = v + lv, r + lr
        best = (max(best[0], v), max(best[1], r))
    return best

app = FastAPI(title="cloudx reservation-api", version="1.0.0")
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
    require_schema(engine, "reservation")

@app.get("/health")
def health():
    return {"status": "ok"}

def _pid(current: dict) -> str:
    return current.get("project_id") or current.get("tenant_id") or "default"

def _call(request: Request, base: str, path: str):
    h = {k: v for k, v in request.headers.items() if k.lower() in ("x-auth-token", "authorization")}
    try:
        r = requests.get(f"{base}{path}", headers=h, timeout=10)
    except requests.RequestException as exc:
        raise HTTPException(503, f"upstream unreachable: {exc}") from None
    if r.status_code == 404:
        raise HTTPException(404, "not found")
    if not r.ok:
        raise HTTPException(502, f"upstream {base}{path} answered {r.status_code}")
    return r.json()

def cluster_capacity(request: Request) -> tuple[int, int]:
    """(vcpus, ram_mb) schedulable in Placement: sum of (total - reserved) x allocation_ratio over all providers."""
    v = r = 0
    for rp in _call(request, PLACEMENT_API_URL, "/resource_providers").get("resource_providers", []):
        inv = _call(request, PLACEMENT_API_URL, f"/resource_providers/{rp['uuid']}/inventories")["inventories"]
        for rc, acc in (("VCPU", "v"), ("MEMORY_MB", "r")):
            if rc in inv:
                n = int((inv[rc]["total"] - inv[rc]["reserved"]) * inv[rc]["allocation_ratio"])
                v, r = (v + n, r) if acc == "v" else (v, r + n)
    if not v or not r:
        raise HTTPException(503, "Placement reports no VCPU / MEMORY_MB inventory; capacity unknown")
    return v, r

def _out(x: Lease) -> dict:
    t = now()
    status = "pending" if t < x.start_at else "active" if t < x.end_at else "expired"
    return {"id": x.id, "name": x.name, "project_id": x.project_id, "start_at": x.start_at.isoformat(), "end_at": x.end_at.isoformat(),
            "flavor": x.flavor, "amount": x.amount, "vcpus": x.vcpus, "ram_mb": x.ram_mb, "status": status}

def _utc(v: datetime) -> datetime:
    return v.astimezone(timezone.utc).replace(tzinfo=None) if v.tzinfo else v

class LeaseIn(BaseModel):
    name: str
    start_at: datetime
    end_at: datetime
    flavor: str
    amount: int = Field(1, ge=1, le=100)

    @field_validator("name")
    @classmethod
    def _name(cls, v):
        if not NAME_RE.match(v):
            raise ValueError("name: 1-64 letters, digits, '.', '_' or '-'")
        return v

def _leases(db: Session, start: datetime, end: datetime):
    q = select(Lease).where(Lease.start_at < end, Lease.end_at > start)
    return [(x.start_at, x.end_at, x.vcpus, x.ram_mb) for x in db.scalars(q)]

@app.post("/leases", status_code=201)
def create_lease(data: LeaseIn, request: Request, current: CurrentUser, db: DB):
    if not owns(current, _pid(current)):
        raise HTTPException(403, "Not allowed")
    start, end = _utc(data.start_at), _utc(data.end_at)
    if start < now() - timedelta(minutes=1):
        raise HTTPException(422, "start_at is in the past")
    if end <= start or end - start > MAX_WINDOW:
        raise HTTPException(422, "end_at must be after start_at, at most 30 days later")
    fl = _call(request, COMPUTE_API_URL, f"/flavors/{data.flavor}")
    v, r = fl["vcpus"] * data.amount, fl["ram_mb"] * data.amount
    cap_v, cap_r = cluster_capacity(request)
    if db.get_bind().dialect.name == "postgresql":  # two admissions at once must not both see the same free capacity
        db.execute(text("SELECT pg_advisory_xact_lock(80230001)"))
    pv, pr = peak(_leases(db, start, end), start, end)
    if pv + v > cap_v or pr + r > cap_r:
        raise HTTPException(409, f"Not enough capacity: free {cap_v - pv} vCPU / {cap_r - pr} MB in that window, need {v} / {r}")
    x = Lease(project_id=_pid(current), name=data.name, start_at=start, end_at=end, flavor=fl["name"], amount=data.amount, vcpus=v, ram_mb=r)
    db.add(x)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Lease name already exists") from None
    return _out(x)

@app.get("/leases")
def list_leases(current: CurrentUser, db: DB):
    ids = visible_projects(current)
    q = select(Lease).order_by(Lease.start_at)
    if ids is not None:
        q = q.where(Lease.project_id.in_(ids or [_pid(current)]))
    return {"items": [_out(x) for x in db.scalars(q)]}

def _get(db: Session, ref: str, current: dict) -> Lease:
    x = db.get(Lease, ref) or db.scalar(select(Lease).where(Lease.project_id == _pid(current), Lease.name == ref))
    if not x or (visible_projects(current) is not None and x.project_id not in (visible_projects(current) or [_pid(current)])):
        raise HTTPException(404, "Lease not found")
    return x

@app.get("/leases/{ref}")
def get_lease(ref: str, current: CurrentUser, db: DB):
    return _out(_get(db, ref, current))

@app.delete("/leases/{ref}", status_code=204)
def delete_lease(ref: str, current: CurrentUser, db: DB):
    x = _get(db, ref, current)
    if not owns(current, x.project_id):
        raise HTTPException(403, "Not allowed")
    db.delete(x)
    db.commit()

@app.get("/capacity")
def capacity(request: Request, current: CurrentUser, db: DB, start: datetime | None = None, end: datetime | None = None):
    s = _utc(start) if start else now()
    e = _utc(end) if end else s + timedelta(hours=1)
    if e <= s or e - s > MAX_WINDOW:
        raise HTTPException(422, "end must be after start, at most 30 days later")
    cap_v, cap_r = cluster_capacity(request)
    pv, pr = peak(_leases(db, s, e), s, e)
    return {"start": s.isoformat(), "end": e.isoformat(), "total": {"vcpus": cap_v, "ram_mb": cap_r},
            "reserved_peak": {"vcpus": pv, "ram_mb": pr}, "free": {"vcpus": cap_v - pv, "ram_mb": cap_r - pr}}
