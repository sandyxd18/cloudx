"""
cloudx Load Balancer API (Octavia-like)
========================================

Supports:
  - NLB (Network Load Balancer / L4) via OVN native LB
  - ALB (Application Load Balancer / L7) via HAProxy

Pools can target a Server Group (compute_server_groups); members are synced
automatically from active instances in that group.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime
from typing import Annotated

import requests
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

import sys
sys.path.insert(0, "/app")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

try:
    from shared.auth_middleware import CurrentUser, AdminUser
except ImportError:
    try:
        from app.auth_middleware import CurrentUser, AdminUser
    except ImportError:
        from auth_middleware import CurrentUser, AdminUser

try:
    from shared.request_id_middleware import RequestIDMiddleware
except ImportError:
    RequestIDMiddleware = None

# ── Config ────────────────────────────────────────────────────────────────────

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://mini:mini@postgres:5432/mini_openstack",
)
AUTH_API_URL = os.getenv("AUTH_API_URL", "http://auth-api:8000").rstrip("/")
NETWORKING_API_URL = os.getenv("NETWORKING_API_URL", "http://networking-api:8000").rstrip("/")
COMPUTE_API_URL = os.getenv("COMPUTE_API_URL", "http://compute-api:8000").rstrip("/")
AUTH_ADMIN_USERNAME = os.getenv("AUTH_ADMIN_USERNAME", "admin")
AUTH_ADMIN_PASSWORD = os.getenv("AUTH_ADMIN_PASSWORD", "admin123")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

_svc_token: str | None = None

def _get_svc_token() -> str | None:
    global _svc_token
    if _svc_token:
        return _svc_token
    try:
        resp = requests.post(
            f"{AUTH_API_URL}/auth/tokens",
            json={"username": AUTH_ADMIN_USERNAME, "password": AUTH_ADMIN_PASSWORD},
            timeout=10,
        )
        if resp.status_code == 201:
            _svc_token = resp.json().get("token")
        else:
            log.warning("load-balancer-api: failed to obtain service token: HTTP %s", resp.status_code)
    except Exception as exc:
        log.warning("load-balancer-api: could not reach auth-api for service token: %s", exc)
    return _svc_token

def _svc_headers() -> dict:
    token = _get_svc_token()
    if token:
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    return {"Content-Type": "application/json"}

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(name)s] %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("load-balancer-api")

# ── Database ──────────────────────────────────────────────────────────────────

engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=5)
SessionLocal = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    pass


def new_id() -> str:
    return str(uuid.uuid4())


def now() -> datetime:
    return datetime.utcnow()


# ── Models ────────────────────────────────────────────────────────────────────

class LoadBalancer(Base):
    __tablename__ = "lb_load_balancers"

    id:              Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name:            Mapped[str] = mapped_column(String, index=True)
    project_id:      Mapped[str] = mapped_column(String, index=True)
    lb_type:         Mapped[str] = mapped_column(String)  # NLB | ALB
    vip_address:     Mapped[str | None] = mapped_column(String, nullable=True)
    vip_network_id:  Mapped[str | None] = mapped_column(String, nullable=True)
    vip_port_id:     Mapped[str | None] = mapped_column(String, nullable=True)
    vip_floating_ip_id: Mapped[str | None] = mapped_column(String, nullable=True)
    provisioning_status: Mapped[str] = mapped_column(String, default="PENDING_CREATE")
    operating_status:    Mapped[str] = mapped_column(String, default="OFFLINE")
    created_at:      Mapped[datetime] = mapped_column(DateTime, default=now)


class Listener(Base):
    __tablename__ = "lb_listeners"

    id:                Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    load_balancer_id:  Mapped[str] = mapped_column(String, ForeignKey("lb_load_balancers.id"))
    name:              Mapped[str] = mapped_column(String)
    protocol:          Mapped[str] = mapped_column(String)  # TCP | HTTP | HTTPS | UDP
    protocol_port:     Mapped[int] = mapped_column(Integer)
    default_pool_id:   Mapped[str | None] = mapped_column(String, nullable=True)
    created_at:        Mapped[datetime] = mapped_column(DateTime, default=now)


class Pool(Base):
    __tablename__ = "lb_pools"

    id:               Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    load_balancer_id: Mapped[str] = mapped_column(String, ForeignKey("lb_load_balancers.id"))
    listener_id:      Mapped[str] = mapped_column(String, ForeignKey("lb_listeners.id"))
    name:             Mapped[str] = mapped_column(String)
    protocol:         Mapped[str] = mapped_column(String)
    lb_algorithm:     Mapped[str] = mapped_column(String, default="ROUND_ROBIN")
    server_group_id:  Mapped[str | None] = mapped_column(String, nullable=True)
    created_at:       Mapped[datetime] = mapped_column(DateTime, default=now)


class Member(Base):
    __tablename__ = "lb_members"

    id:               Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    pool_id:          Mapped[str] = mapped_column(String, ForeignKey("lb_pools.id"))
    instance_id:      Mapped[str | None] = mapped_column(String, nullable=True)
    address:          Mapped[str] = mapped_column(String)
    protocol_port:    Mapped[int] = mapped_column(Integer)
    weight:           Mapped[int] = mapped_column(Integer, default=1)
    operating_status: Mapped[str] = mapped_column(String, default="ONLINE")
    created_at:       Mapped[datetime] = mapped_column(DateTime, default=now)


class HealthMonitor(Base):
    __tablename__ = "lb_health_monitors"

    id:          Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    pool_id:     Mapped[str] = mapped_column(String, ForeignKey("lb_pools.id"))
    type:        Mapped[str] = mapped_column(String)  # HTTP | TCP | PING
    delay:       Mapped[int] = mapped_column(Integer, default=5)
    timeout:     Mapped[int] = mapped_column(Integer, default=5)
    max_retries: Mapped[int] = mapped_column(Integer, default=3)
    url_path:    Mapped[str] = mapped_column(String, default="/")
    created_at:  Mapped[datetime] = mapped_column(DateTime, default=now)


# ── Schemas ───────────────────────────────────────────────────────────────────

class LoadBalancerIn(BaseModel):
    name: str
    lb_type: str = Field(..., pattern="^(NLB|ALB)$")
    vip_network_id: str
    vip_subnet_id: str | None = None


class ListenerIn(BaseModel):
    name: str
    protocol: str = Field(..., pattern="^(TCP|UDP|HTTP|HTTPS)$")
    protocol_port: int = Field(..., ge=1, le=65535)


class PoolMemberIn(BaseModel):
    name: str | None = None
    address: str
    protocol_port: int | None = None
    instance_id: str | None = None


class PoolIn(BaseModel):
    name: str
    lb_algorithm: str = Field(default="ROUND_ROBIN", pattern="^(ROUND_ROBIN|LEAST_CONNECTIONS|SOURCE_IP)$")
    server_group_id: str | None = None
    protocol_port: int = Field(..., ge=1, le=65535)
    members: list[PoolMemberIn] | None = None


class HealthMonitorIn(BaseModel):
    type: str = Field(..., pattern="^(HTTP|TCP|PING)$")
    delay: int = Field(default=5, ge=1)
    timeout: int = Field(default=5, ge=1)
    max_retries: int = Field(default=3, ge=1)
    url_path: str = Field(default="/")


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(title="cloudx load-balancer-api", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
if RequestIDMiddleware:
    app.add_middleware(RequestIDMiddleware)


# ── DB dependency ─────────────────────────────────────────────────────────────

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


DB = Annotated[Session, Depends(get_db)]


@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _project_id(current: dict) -> str:
    return current.get("project_id") or current.get("tenant_id") or "default"


def _get_driver(lb: LoadBalancer):
    from app.drivers import get_driver
    driver_cls = get_driver(lb.lb_type)
    return driver_cls({})


try:
    from shared.service_client import ServiceClient
except ImportError:
    from service_client import ServiceClient

service_client = ServiceClient(service_name="service_load_balancer")


def _allocate_vip(network_id: str, subnet_id: str | None) -> dict:
    """Allocate a fixed port/IP from networking-api."""
    try:
        payload = {"name": f"lb-vip-{new_id()}", "network": network_id}
        if subnet_id:
            payload["fixed_ips"] = [{"subnet_id": subnet_id}]
        resp = service_client.post(
            f"{NETWORKING_API_URL}/ports",
            json=payload,
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        log.error("VIP allocation failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"VIP allocation failed: {exc}")


def _release_vip(port_id: str | None):
    if not port_id:
        return
    try:
        service_client.delete(f"{NETWORKING_API_URL}/ports/{port_id}", timeout=10)
    except Exception as exc:
        log.warning("Failed to release VIP port %s: %s", port_id, exc)


def _fetch_server_group_members(server_group_id: str, project_id: str) -> list[dict]:
    """Fetch instances from compute-api belonging to a server group."""
    try:
        resp = service_client.get(
            f"{COMPUTE_API_URL}/servers",
            params={"server_group_id": server_group_id},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        members = []
        for srv in data.get("items", []):
            if srv.get("project_id") != project_id:
                continue
            addr = srv.get("fixed_ip") or srv.get("ip_address")
            if addr:
                members.append({
                    "instance_id": srv["id"],
                    "address": addr,
                    "protocol_port": 80,  # default; overwritten by pool
                })
        return members
    except Exception as exc:
        log.error("Failed to fetch server group members: %s", exc)
        return []


def _lb_to_dict(lb: LoadBalancer) -> dict:
    return {
        "id": lb.id,
        "name": lb.name,
        "project_id": lb.project_id,
        "lb_type": lb.lb_type,
        "vip_address": lb.vip_address,
        "vip_network_id": lb.vip_network_id,
        "vip_port_id": lb.vip_port_id,
        "vip_floating_ip_id": lb.vip_floating_ip_id,
        "provisioning_status": lb.provisioning_status,
        "operating_status": lb.operating_status,
        "created_at": lb.created_at.isoformat() if lb.created_at else None,
    }


def _listener_to_dict(ls: Listener) -> dict:
    return {
        "id": ls.id,
        "load_balancer_id": ls.load_balancer_id,
        "name": ls.name,
        "protocol": ls.protocol,
        "protocol_port": ls.protocol_port,
        "default_pool_id": ls.default_pool_id,
        "created_at": ls.created_at.isoformat() if ls.created_at else None,
    }


def _pool_to_dict(pool: Pool) -> dict:
    return {
        "id": pool.id,
        "load_balancer_id": pool.load_balancer_id,
        "listener_id": pool.listener_id,
        "name": pool.name,
        "protocol": pool.protocol,
        "lb_algorithm": pool.lb_algorithm,
        "server_group_id": pool.server_group_id,
        "created_at": pool.created_at.isoformat() if pool.created_at else None,
    }


def _member_to_dict(m: Member) -> dict:
    return {
        "id": m.id,
        "pool_id": m.pool_id,
        "instance_id": m.instance_id,
        "address": m.address,
        "protocol_port": m.protocol_port,
        "weight": m.weight,
        "operating_status": m.operating_status,
    }


def _apply_lb_state(db: Session, lb: LoadBalancer):
    """Push current DB state to the data-plane driver."""
    driver = _get_driver(lb)
    listeners = db.query(Listener).filter_by(load_balancer_id=lb.id).all()
    driver.create_load_balancer(_lb_to_dict(lb))
    for ls in listeners:
        pool = db.query(Pool).filter_by(listener_id=ls.id).first()
        members = []
        if pool:
            members = db.query(Member).filter_by(pool_id=pool.id).all()
            members = [_member_to_dict(m) for m in members]
        driver.apply_listener(_lb_to_dict(lb), {**_listener_to_dict(ls), "members": members})
        if pool:
            driver.apply_pool(
                _lb_to_dict(lb),
                _listener_to_dict(ls),
                _pool_to_dict(pool),
                members,
            )


# ═══════════════════════════════════════════════════════════════════════════════
# Load Balancers
# ═══════════════════════════════════════════════════════════════════════════════

@app.post("/load-balancers", status_code=201)
def create_load_balancer(payload: LoadBalancerIn, current: CurrentUser, db: DB):
    project_id = _project_id(current)

    vip = _allocate_vip(payload.vip_network_id, payload.vip_subnet_id)
    vip_address = vip.get("fixed_ips", [{}])[0].get("ip_address")
    vip_port_id = vip.get("id")

    lb = LoadBalancer(
        name=payload.name,
        project_id=project_id,
        lb_type=payload.lb_type,
        vip_address=vip_address,
        vip_network_id=payload.vip_network_id,
        vip_port_id=vip_port_id,
        provisioning_status="ACTIVE",
        operating_status="ONLINE",
    )
    db.add(lb)
    db.commit()

    try:
        _apply_lb_state(db, lb)
    except Exception as exc:
        log.error("Failed to apply LB state for %s: %s", lb.id, exc)
        lb.provisioning_status = "ERROR"
        db.commit()

    return _lb_to_dict(lb)


@app.get("/load-balancers")
def list_load_balancers(current: CurrentUser, db: DB):
    project_id = _project_id(current)
    rows = db.query(LoadBalancer).filter_by(project_id=project_id).all()
    return {"load_balancers": [_lb_to_dict(r) for r in rows]}


@app.get("/load-balancers/{ref}")
def show_load_balancer(ref: str, current: CurrentUser, db: DB):
    project_id = _project_id(current)
    lb = db.query(LoadBalancer).filter_by(id=ref, project_id=project_id).first()
    if not lb:
        raise HTTPException(status_code=404, detail="load balancer not found")
    return _lb_to_dict(lb)


@app.delete("/load-balancers/{ref}")
def delete_load_balancer(ref: str, current: CurrentUser, db: DB):
    project_id = _project_id(current)
    lb = db.query(LoadBalancer).filter_by(id=ref, project_id=project_id).first()
    if not lb:
        raise HTTPException(status_code=404, detail="load balancer not found")

    driver = _get_driver(lb)
    try:
        driver.delete_load_balancer(_lb_to_dict(lb))
    except Exception as exc:
        log.warning("Driver delete failed for %s: %s", ref, exc)

    _release_vip(lb.vip_port_id)
    db.query(Member).filter(Member.pool_id.in_(
        db.query(Pool.id).filter_by(load_balancer_id=lb.id)
    )).delete(synchronize_session=False)
    db.query(Pool).filter_by(load_balancer_id=lb.id).delete()
    db.query(Listener).filter_by(load_balancer_id=lb.id).delete()
    db.delete(lb)
    db.commit()
    return {"status": "deleted", "id": ref}


# ═══════════════════════════════════════════════════════════════════════════════
# Listeners
# ═══════════════════════════════════════════════════════════════════════════════

@app.post("/load-balancers/{lb_ref}/listeners", status_code=201)
def create_listener(lb_ref: str, payload: ListenerIn, current: CurrentUser, db: DB):
    project_id = _project_id(current)
    lb = db.query(LoadBalancer).filter_by(id=lb_ref, project_id=project_id).first()
    if not lb:
        raise HTTPException(status_code=404, detail="load balancer not found")

    ls = Listener(
        load_balancer_id=lb.id,
        name=payload.name,
        protocol=payload.protocol,
        protocol_port=payload.protocol_port,
    )
    db.add(ls)
    db.commit()

    try:
        driver = _get_driver(lb)
        driver.apply_listener(_lb_to_dict(lb), {**_listener_to_dict(ls), "members": []})
    except Exception as exc:
        log.error("Failed to apply listener %s: %s", ls.id, exc)

    return _listener_to_dict(ls)


@app.get("/load-balancers/{lb_ref}/listeners")
def list_listeners(lb_ref: str, current: CurrentUser, db: DB):
    project_id = _project_id(current)
    lb = db.query(LoadBalancer).filter_by(id=lb_ref, project_id=project_id).first()
    if not lb:
        raise HTTPException(status_code=404, detail="load balancer not found")
    rows = db.query(Listener).filter_by(load_balancer_id=lb.id).all()
    return {"listeners": [_listener_to_dict(r) for r in rows]}


# ═══════════════════════════════════════════════════════════════════════════════
# Pools (Server Group target)
# ═══════════════════════════════════════════════════════════════════════════════

@app.post("/listeners/{ls_ref}/pools", status_code=201)
def create_pool(ls_ref: str, payload: PoolIn, current: CurrentUser, db: DB):
    project_id = _project_id(current)
    ls = db.query(Listener).filter_by(id=ls_ref).first()
    if not ls:
        raise HTTPException(status_code=404, detail="listener not found")
    lb = db.query(LoadBalancer).filter_by(id=ls.load_balancer_id, project_id=project_id).first()
    if not lb:
        raise HTTPException(status_code=404, detail="load balancer not found")

    pool = Pool(
        load_balancer_id=lb.id,
        listener_id=ls.id,
        name=payload.name,
        protocol=ls.protocol,
        lb_algorithm=payload.lb_algorithm,
        server_group_id=payload.server_group_id,
    )
    db.add(pool)
    ls.default_pool_id = pool.id
    db.commit()

    members: list[dict] = []
    if payload.members:
        for idx, item in enumerate(payload.members):
            m_dict = {
                "name": item.name or f"member-{idx+1}",
                "address": item.address,
                "protocol_port": item.protocol_port or payload.protocol_port,
                "instance_id": item.instance_id,
                "weight": 1,
            }
            m = Member(pool_id=pool.id, **m_dict)
            db.add(m)
            members.append(_member_to_dict(m))
        db.commit()
    elif payload.server_group_id:
        raw = _fetch_server_group_members(payload.server_group_id, project_id)
        for item in raw:
            item["protocol_port"] = payload.protocol_port
            m = Member(pool_id=pool.id, **item)
            db.add(m)
            members.append(_member_to_dict(m))
        db.commit()

    try:
        driver = _get_driver(lb)
        hms = db.query(HealthMonitor).filter_by(pool_id=pool.id).all()
        ls_dict = {**_listener_to_dict(ls), "health_monitors": [_hm_to_dict(h) for h in hms]}
        driver.apply_pool(_lb_to_dict(lb), ls_dict, _pool_to_dict(pool), members)
    except Exception as exc:
        log.error("Failed to apply pool %s: %s", pool.id, exc)

    return _pool_to_dict(pool)


@app.get("/pools/{pool_ref}")
def show_pool(pool_ref: str, current: CurrentUser, db: DB):
    pool = db.query(Pool).filter_by(id=pool_ref).first()
    if not pool:
        raise HTTPException(status_code=404, detail="pool not found")
    lb = db.query(LoadBalancer).filter_by(id=pool.load_balancer_id).first()
    if lb and lb.project_id != _project_id(current):
        raise HTTPException(status_code=404, detail="pool not found")
    members = db.query(Member).filter_by(pool_id=pool.id).all()
    return {
        ** _pool_to_dict(pool),
        "members": [_member_to_dict(m) for m in members],
    }


def _hm_to_dict(hm: HealthMonitor) -> dict:
    return {
        "id": hm.id,
        "pool_id": hm.pool_id,
        "type": hm.type,
        "delay": hm.delay,
        "timeout": hm.timeout,
        "max_retries": hm.max_retries,
        "url_path": hm.url_path,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Health Monitors
# ═══════════════════════════════════════════════════════════════════════════════

@app.post("/pools/{pool_ref}/health-monitors", status_code=201)
def create_health_monitor(pool_ref: str, payload: HealthMonitorIn, current: CurrentUser, db: DB):
    pool = db.query(Pool).filter_by(id=pool_ref).first()
    if not pool:
        raise HTTPException(status_code=404, detail="pool not found")
    lb = db.query(LoadBalancer).filter_by(id=pool.load_balancer_id).first()
    if lb and lb.project_id != _project_id(current):
        raise HTTPException(status_code=404, detail="pool not found")

    hm = HealthMonitor(
        pool_id=pool.id,
        type=payload.type,
        delay=payload.delay,
        timeout=payload.timeout,
        max_retries=payload.max_retries,
        url_path=payload.url_path,
    )
    db.add(hm)
    db.commit()

    try:
        _apply_lb_state(db, lb)
    except Exception as exc:
        log.error("Failed to re-apply LB after health monitor: %s", exc)

    return _hm_to_dict(hm)


# ═══════════════════════════════════════════════════════════════════════════════
# Sync members from Server Group
# ═══════════════════════════════════════════════════════════════════════════════

@app.post("/pools/{pool_ref}/sync")
def sync_pool_members(pool_ref: str, current: CurrentUser, db: DB):
    pool = db.query(Pool).filter_by(id=pool_ref).first()
    if not pool or not pool.server_group_id:
        raise HTTPException(status_code=400, detail="pool has no server group")
    lb = db.query(LoadBalancer).filter_by(id=pool.load_balancer_id).first()
    if lb and lb.project_id != _project_id(current):
        raise HTTPException(status_code=404, detail="pool not found")

    project_id = _project_id(current)
    raw = _fetch_server_group_members(pool.server_group_id, project_id)

    # Replace all auto-synced members
    db.query(Member).filter_by(pool_id=pool.id).delete()
    members = []
    for item in raw:
        item["protocol_port"] = pool.protocol_port if hasattr(pool, "protocol_port") else 80
        m = Member(pool_id=pool.id, **item)
        db.add(m)
        members.append(_member_to_dict(m))
    db.commit()

    try:
        _apply_lb_state(db, lb)
    except Exception as exc:
        log.error("Failed to apply synced members: %s", exc)

    return {"members": members}


# ═══════════════════════════════════════════════════════════════════════════════
# Health
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/health")
def health():
    return {"status": "ok", "service": "load-balancer-api"}


@app.get("/healthcheck")
def healthcheck(db: DB):
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ok", "checks": {"db": True}}
    except Exception as exc:
        return {"status": "error", "checks": {"db": False, "error": str(exc)}}
