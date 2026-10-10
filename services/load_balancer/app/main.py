from __future__ import annotations
import os
from shared.config import load_service_config
load_service_config("load-balancer-api")

"""
cloudx Load Balancer API (Octavia-like)
========================================

Supports:
  - NLB (Network Load Balancer / L4) via OVN native LB
  - ALB (Application Load Balancer / L7) via HAProxy

Pools can target a Server Group (compute_server_groups); members are synced
automatically from active instances in that group.
"""

import logging
import threading
import time
import uuid
from datetime import datetime
from typing import Annotated

import requests
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import ipaddress

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import DateTime, ForeignKey, Integer, String, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


from shared.auth_middleware import CurrentUser, InternalAuth
from shared.schema import require_schema
from shared.request_id_middleware import RequestIDMiddleware

# ── Config ────────────────────────────────────────────────────────────────────

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://cloudx:cloudx@127.0.0.1:5432/cloudx",
)
AUTH_API_URL = os.getenv("AUTH_API_URL", "http://auth-api:8000").rstrip("/")
NETWORKING_API_URL = os.getenv("NETWORKING_API_URL", "http://networking-api:8000").rstrip("/")
COMPUTE_API_URL = os.getenv("COMPUTE_API_URL", "http://compute-api:8000").rstrip("/")
AUTH_ADMIN_USERNAME = os.getenv("AUTH_ADMIN_USERNAME", "admin")
AUTH_ADMIN_PASSWORD = os.environ["AUTH_ADMIN_PASSWORD"]
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
    vip_floating_ip: Mapped[str | None] = mapped_column(String, nullable=True)
    vip_mac:         Mapped[str | None] = mapped_column(String, nullable=True)
    vip_port_name:   Mapped[str | None] = mapped_column(String, nullable=True)
    vip_switch:      Mapped[str | None] = mapped_column(String, nullable=True)
    vip_prefix:      Mapped[int | None] = mapped_column(Integer, nullable=True)
    vip_gateway:     Mapped[str | None] = mapped_column(String, nullable=True)
    host:            Mapped[str | None] = mapped_column(String, nullable=True)  # compute node running the ALB haproxy
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
    certificate_id:    Mapped[str | None] = mapped_column(String, nullable=True)
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


class L7Policy(Base):
    __tablename__ = "lb_l7policies"

    id:               Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    listener_id:      Mapped[str] = mapped_column(String, ForeignKey("lb_listeners.id"))
    name:             Mapped[str] = mapped_column(String)
    action:           Mapped[str] = mapped_column(String)  # REDIRECT_TO_POOL | REJECT
    position:         Mapped[int] = mapped_column(Integer, default=1)
    redirect_pool_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at:       Mapped[datetime] = mapped_column(DateTime, default=now)


class L7Rule(Base):
    __tablename__ = "lb_l7rules"

    id:           Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    policy_id:    Mapped[str] = mapped_column(String, ForeignKey("lb_l7policies.id"))
    type:         Mapped[str] = mapped_column(String)          # PATH | HOST_NAME
    compare_type: Mapped[str] = mapped_column(String)          # STARTS_WITH | ENDS_WITH | EQUAL_TO
    value:        Mapped[str] = mapped_column(String)
    created_at:   Mapped[datetime] = mapped_column(DateTime, default=now)


class LBCertificate(Base):
    __tablename__ = "lb_certificates"

    id:               Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    load_balancer_id: Mapped[str] = mapped_column(String, ForeignKey("lb_load_balancers.id"))
    name:             Mapped[str] = mapped_column(String)
    certificate_pem:  Mapped[str] = mapped_column(String)
    private_key_pem:  Mapped[str] = mapped_column(String)
    created_at:       Mapped[datetime] = mapped_column(DateTime, default=now)


# ── Schemas ───────────────────────────────────────────────────────────────────

NAME = Field(..., pattern=r"^[\w .-]{1,128}$")


class LoadBalancerIn(BaseModel):
    name: str = NAME
    lb_type: str = Field(..., pattern="^(NLB|ALB)$")
    vip_network_id: str
    vip_subnet_id: str | None = None
    floating_network: str | None = None  # also give the VIP a floating IP from this external network


class ListenerIn(BaseModel):
    name: str = NAME
    protocol: str = Field(..., pattern="^(TCP|UDP|HTTP|HTTPS)$")
    protocol_port: int = Field(..., ge=1, le=65535)
    certificate_id: str | None = None


class CertificateIn(BaseModel):
    name: str = NAME
    certificate_pem: str
    private_key_pem: str


class PoolMemberIn(BaseModel):
    name: str | None = Field(default=None, pattern=r"^[\w .-]{1,128}$")
    address: str
    protocol_port: int | None = None
    instance_id: str | None = None

    @field_validator("address")
    @classmethod
    def _v_addr(cls, v):
        return str(ipaddress.ip_address(v))


class PoolIn(BaseModel):
    name: str = NAME
    lb_algorithm: str = Field(default="ROUND_ROBIN", pattern="^(ROUND_ROBIN|LEAST_CONNECTIONS|SOURCE_IP)$")
    server_group_id: str | None = None
    protocol_port: int = Field(..., ge=1, le=65535)
    members: list[PoolMemberIn] | None = None


class PoolUpdate(BaseModel):
    lb_algorithm: str = Field(pattern="^(ROUND_ROBIN|LEAST_CONNECTIONS|SOURCE_IP)$")


class L7PolicyIn(BaseModel):
    name: str = NAME
    action: str = Field(default="REDIRECT_TO_POOL", pattern="^(REDIRECT_TO_POOL|REJECT)$")
    position: int = Field(default=1, ge=1, le=1000)
    redirect_pool_id: str | None = None


class L7RuleIn(BaseModel):
    type: str = Field(pattern="^(PATH|HOST_NAME)$")
    compare_type: str = Field(default="STARTS_WITH", pattern="^(STARTS_WITH|ENDS_WITH|EQUAL_TO)$")
    value: str = Field(pattern=r"^[A-Za-z0-9._~/%?&=+*:-]{1,200}$")


class HealthMonitorIn(BaseModel):
    type: str = Field(..., pattern="^(HTTP|TCP|PING)$")
    delay: int = Field(default=5, ge=1)
    timeout: int = Field(default=5, ge=1)
    max_retries: int = Field(default=3, ge=1)
    url_path: str = Field(default="/", pattern=r"^/[A-Za-z0-9._~/%?&=+-]{0,200}$")


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(title="cloudx load-balancer-api", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost").split(",") if o.strip() and o.strip() != "*"],
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


def _sync_nlb_status():
    """NLB members: OVN (ovn-controller) probes them and publishes the result as Service_Monitor rows in the SB DB.

    ALB members are reported by lb-worker instead (it reads haproxy's own health checks).
    """
    from .drivers.ovn_l4 import service_monitor_status

    status = service_monitor_status()  # {(ip, port): "online"|"offline"}
    if not status:
        return
    with SessionLocal() as db:
        lb_ids = [r.id for r in db.query(LoadBalancer.id).filter_by(lb_type="NLB")]
        rows = db.query(Member).join(Pool, Member.pool_id == Pool.id).filter(Pool.load_balancer_id.in_(lb_ids)).all()
        for m in rows:
            s = status.get((m.address, m.protocol_port))
            if s and m.operating_status != s.upper():
                m.operating_status = s.upper()
        db.commit()


def _status_loop():
    while True:
        time.sleep(10)
        try:
            _sync_nlb_status()
        except Exception as exc:  # best-effort: status is informational, the loop must survive
            log.debug("nlb status sync failed: %s", exc)


@app.on_event("startup")
def startup():
    require_schema(engine, "load_balancer")
    threading.Thread(target=_status_loop, daemon=True).start()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _project_id(current: dict) -> str:
    return current.get("project_id") or current.get("tenant_id") or "default"


def _get_driver(lb: LoadBalancer):
    from .drivers import get_driver
    driver_cls = get_driver(lb.lb_type)
    return driver_cls({})


from shared.service_client import ServiceClient

service_client = ServiceClient(service_name="service_load_balancer")


def _net(method: str, path: str, **kw) -> dict:
    r = service_client.request(method, f"{NETWORKING_API_URL}{path}", timeout=15, **kw)
    r.raise_for_status()
    return r.json() if r.content else {}


def _allocate_vip(network_id: str, subnet_id: str | None) -> dict:
    """Allocate the VIP port and look up what the data plane needs to answer for it (switch, mask, gateway)."""
    try:
        payload = {"name": f"lb-vip-{new_id()}", "network": network_id, "device_owner": "cloudx:loadbalancer"}
        if subnet_id:
            payload["fixed_ips"] = [{"subnet_id": subnet_id}]
        port = _net("POST", "/ports", json=payload)
        fixed = port["fixed_ips"][0]
        subnet = _net("GET", f"/subnets/{fixed['subnet_id']}")
        net = _net("GET", f"/networks/{port['network_id']}")
        return {**port, "ip": fixed["ip_address"], "switch": net["name"],
                "prefix": int(subnet["cidr"].split("/")[1]), "gateway": subnet.get("gateway_ip")}
    except Exception as exc:
        log.error("VIP allocation failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"VIP allocation failed: {exc}")


def _port_names() -> dict[str, str]:
    """fixed ip -> OVN logical port name, so OVN health checks know which port a backend lives on."""
    try:
        items = _net("GET", "/ports").get("items", [])
        return {p["fixed_ip"]: p["logical_port_name"] for p in items if p.get("fixed_ip") and p.get("logical_port_name")}
    except Exception as exc:
        log.warning("port lookup failed: %s", exc)
        return {}


def _release_vip(lb: LoadBalancer):
    for path in ([f"/floating-ips/{lb.vip_floating_ip_id}"] if lb.vip_floating_ip_id else []) + \
                ([f"/ports/{lb.vip_port_id}"] if lb.vip_port_id else []):
        try:
            if "floating" in path:
                _net("POST", path + "/disassociate", json={})
            _net("DELETE", path)
        except Exception as exc:
            log.warning("Failed to release %s: %s", path, exc)


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
        "vip_floating_ip": lb.vip_floating_ip,
        "vip_mac": lb.vip_mac,
        "vip_port_name": lb.vip_port_name,
        "vip_switch": lb.vip_switch,
        "vip_prefix": lb.vip_prefix,
        "vip_gateway": lb.vip_gateway,
        "host": lb.host,
        "provisioning_status": lb.provisioning_status,
        "operating_status": lb.operating_status,
        "created_at": lb.created_at.isoformat() if lb.created_at else None,
    }


def _listener_to_dict(ls: Listener, db: Session | None = None, with_pem: bool = False) -> dict:
    # ssl_cert_path is where lb-worker keeps the PEM on its own node; the PEM itself only leaves through /internal/lb-agent
    ssl_cert_path = pem = None
    if ls.certificate_id and db:
        cert = db.get(LBCertificate, ls.certificate_id)
        if cert:
            ssl_cert_path = f"/var/lib/cloudx/lb/{ls.load_balancer_id}/{cert.id}.pem"
            if with_pem:
                pem = f"{cert.certificate_pem}\n{cert.private_key_pem}"
    return {
        "id": ls.id,
        "load_balancer_id": ls.load_balancer_id,
        "name": ls.name,
        "protocol": ls.protocol,
        "protocol_port": ls.protocol_port,
        "default_pool_id": ls.default_pool_id,
        "certificate_id": ls.certificate_id,
        "ssl_cert_path": ssl_cert_path,
        **({"ssl_pem": pem} if pem else {}),
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


def _pick_host() -> str | None:
    """Compute node that will run an ALB's haproxy: the first enabled one."""
    try:
        r = service_client.get(f"{COMPUTE_API_URL}/os-services", timeout=10)
        r.raise_for_status()
        return next((s["host"] for s in r.json()["services"] if s["status"] == "enabled" and not s.get("forced_down")), None)
    except Exception as exc:
        log.warning("could not pick a compute host: %s", exc)
        return None


def lb_state(db: Session, lb: LoadBalancer, with_pem: bool = False) -> list[dict]:
    """The whole desired state of one LB as a tree (see drivers/base.py); drivers and lb-worker both consume it."""
    out = []
    pools = {p.id: p for p in db.query(Pool).filter_by(load_balancer_id=lb.id)}
    names = _port_names() if lb.lb_type == "NLB" else {}
    for ls in db.query(Listener).filter_by(load_balancer_id=lb.id).order_by(Listener.created_at):
        d = {**_listener_to_dict(ls, db, with_pem), "pools": [], "policies": []}
        wanted = {p for p in [ls.default_pool_id] if p}
        for pol in db.query(L7Policy).filter_by(listener_id=ls.id):
            rules = [{"type": r.type, "compare_type": r.compare_type, "value": r.value}
                     for r in db.query(L7Rule).filter_by(policy_id=pol.id)]
            d["policies"].append({"action": pol.action, "redirect_pool_id": pol.redirect_pool_id,
                                  "position": pol.position, "rules": rules})
            if pol.redirect_pool_id:
                wanted.add(pol.redirect_pool_id)
        for pid in wanted:
            p = pools.get(pid)
            if not p:
                continue
            hm = db.query(HealthMonitor).filter_by(pool_id=p.id).first()
            members = [{**_member_to_dict(m), "port_name": names.get(m.address)}
                       for m in db.query(Member).filter_by(pool_id=p.id)]
            d["pools"].append({**_pool_to_dict(p), "members": members, "health_monitor": _hm_to_dict(hm) if hm else None})
        out.append(d)
    return out


def _apply_lb_state(db: Session, lb: LoadBalancer):
    """Reconcile the data plane with the DB. NLB: OVN now. ALB: validate now, lb-worker applies it on its node."""
    _get_driver(lb).apply(_lb_to_dict(lb), lb_state(db, lb))

# ═══════════════════════════════════════════════════════════════════════════════
# Load Balancers
# ═══════════════════════════════════════════════════════════════════════════════

@app.post("/load-balancers", status_code=201)
def create_load_balancer(payload: LoadBalancerIn, current: CurrentUser, db: DB):
    project_id = _project_id(current)

    vip = _allocate_vip(payload.vip_network_id, payload.vip_subnet_id)
    lb = LoadBalancer(
        name=payload.name,
        project_id=project_id,
        lb_type=payload.lb_type,
        vip_address=vip["ip"],
        vip_network_id=payload.vip_network_id,
        vip_port_id=vip["id"],
        vip_port_name=vip["name"],
        vip_mac=vip["mac_address"],
        vip_switch=vip["switch"],
        vip_prefix=vip["prefix"],
        vip_gateway=vip["gateway"],
        host=_pick_host() if payload.lb_type == "ALB" else None,
        provisioning_status="ACTIVE",
        operating_status="ONLINE",
    )
    if payload.lb_type == "ALB" and not lb.host:
        _release_vip(lb.__class__(vip_port_id=vip["id"]))
        raise HTTPException(status_code=503, detail="no enabled compute node to run the ALB on")
    db.add(lb)
    db.commit()
    if payload.floating_network:
        try:
            fip = _net("POST", "/floating-ips", json={"external_network": payload.floating_network})
            lb.vip_floating_ip_id, lb.vip_floating_ip = fip["id"], fip["floating_ip_address"]
            _net("POST", f"/floating-ips/{fip['id']}/associate", json={"port": vip["id"]})
            db.commit()
        except Exception as exc:
            log.error("floating ip for LB %s failed: %s", lb.id, exc)
            lb.provisioning_status = "ERROR"
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

    try:
        _get_driver(lb).delete(_lb_to_dict(lb))
    except Exception as exc:
        log.warning("Driver delete failed for %s: %s", ref, exc)

    _release_vip(lb)
    pool_ids = db.query(Pool.id).filter_by(load_balancer_id=lb.id)
    db.query(Member).filter(Member.pool_id.in_(pool_ids)).delete(synchronize_session=False)
    db.query(HealthMonitor).filter(HealthMonitor.pool_id.in_(pool_ids)).delete(synchronize_session=False)
    ls_ids = db.query(Listener.id).filter_by(load_balancer_id=lb.id)
    pol_ids = db.query(L7Policy.id).filter(L7Policy.listener_id.in_(ls_ids))
    db.query(L7Rule).filter(L7Rule.policy_id.in_(pol_ids)).delete(synchronize_session=False)
    db.query(L7Policy).filter(L7Policy.listener_id.in_(ls_ids)).delete(synchronize_session=False)
    db.query(Pool).filter_by(load_balancer_id=lb.id).delete()
    db.query(Listener).filter_by(load_balancer_id=lb.id).delete()
    db.query(LBCertificate).filter_by(load_balancer_id=lb.id).delete()
    db.delete(lb)
    db.commit()
    return {"status": "deleted", "id": ref}


# ═══════════════════════════════════════════════════════════════════════════════
# Listeners
# ═══════════════════════════════════════════════════════════════════════════════

@app.post("/load-balancers/{lb_ref}/certificates", status_code=201)
def upload_certificate(lb_ref: str, payload: CertificateIn, current: CurrentUser, db: DB):
    project_id = _project_id(current)
    lb = db.query(LoadBalancer).filter_by(id=lb_ref, project_id=project_id).first()
    if not lb:
        raise HTTPException(status_code=404, detail="load balancer not found")
    cert = LBCertificate(
        load_balancer_id=lb.id,
        name=payload.name,
        certificate_pem=payload.certificate_pem,
        private_key_pem=payload.private_key_pem,
    )
    db.add(cert)
    db.commit()
    db.refresh(cert)
    return {"id": cert.id, "name": cert.name, "created_at": cert.created_at.isoformat()}


@app.get("/load-balancers/{lb_ref}/certificates")
def list_certificates(lb_ref: str, current: CurrentUser, db: DB):
    project_id = _project_id(current)
    lb = db.query(LoadBalancer).filter_by(id=lb_ref, project_id=project_id).first()
    if not lb:
        raise HTTPException(status_code=404, detail="load balancer not found")
    certs = db.query(LBCertificate).filter_by(load_balancer_id=lb.id).all()
    return {"certificates": [{"id": c.id, "name": c.name, "created_at": c.created_at.isoformat()} for c in certs]}


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
        certificate_id=payload.certificate_id,
    )
    db.add(ls)
    db.commit()

    try:
        _apply_lb_state(db, lb)
    except Exception as exc:
        log.error("Failed to apply listener %s: %s", ls.id, exc)

    return _listener_to_dict(ls, db)


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
    db.flush()  # assigns pool.id
    ls.default_pool_id = ls.default_pool_id or pool.id  # later pools are targets for L7 policies
    db.commit()

    members: list[dict] = []
    if payload.members:
        for item in payload.members:
            m_dict = {
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
        _apply_lb_state(db, lb)
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

class MemberIn(BaseModel):
    address: str
    protocol_port: int | None = Field(default=None, ge=1, le=65535)
    weight: int = Field(default=1, ge=0, le=256)
    instance_id: str | None = None


def _own_pool(db, current, pool_ref: str):
    pool = db.query(Pool).filter_by(id=pool_ref).first()
    lb = pool and db.query(LoadBalancer).filter_by(id=pool.load_balancer_id).first()
    if not pool or (lb and current.get("role") != "admin" and lb.project_id != _project_id(current)):
        raise HTTPException(status_code=404, detail="pool not found")
    return pool, lb


@app.post("/pools/{pool_ref}/members", status_code=201)
def add_pool_member(pool_ref: str, payload: MemberIn, current: CurrentUser, db: DB):
    pool, lb = _own_pool(db, current, pool_ref)
    m = Member(pool_id=pool.id, address=payload.address, weight=payload.weight,
               instance_id=payload.instance_id, protocol_port=payload.protocol_port or 80)
    db.add(m)
    db.commit()
    try:
        _apply_lb_state(db, lb)
    except Exception as exc:
        log.error("Failed to apply LB after adding member: %s", exc)
    return _member_to_dict(m)


@app.delete("/pools/{pool_ref}/members/{instance_id}", status_code=204)
def remove_pool_member(pool_ref: str, instance_id: str, current: CurrentUser, db: DB):
    """Remove by member id or by instance_id (what the autoscaler knows)."""
    pool, lb = _own_pool(db, current, pool_ref)
    q = db.query(Member).filter(Member.pool_id == pool.id, (Member.id == instance_id) | (Member.instance_id == instance_id))
    if not q.delete(synchronize_session=False):
        raise HTTPException(status_code=404, detail="member not found")
    db.commit()
    try:
        _apply_lb_state(db, lb)
    except Exception as exc:
        log.error("Failed to apply LB after removing member: %s", exc)

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
# Pool update, deletes, L7 policies and rules
# ═══════════════════════════════════════════════════════════════════════════════

def _reapply(db, lb):
    try:
        _apply_lb_state(db, lb)
    except Exception as exc:
        log.error("Failed to apply LB %s: %s", lb.id, exc)


def _own_lb(db, current, lb_ref: str) -> LoadBalancer:
    lb = db.query(LoadBalancer).filter_by(id=lb_ref).first()
    if not lb or (current.get("role") != "admin" and lb.project_id != _project_id(current)):
        raise HTTPException(status_code=404, detail="load balancer not found")
    return lb


def _own_listener(db, current, ls_ref: str):
    ls = db.query(Listener).filter_by(id=ls_ref).first()
    if not ls:
        raise HTTPException(status_code=404, detail="listener not found")
    return ls, _own_lb(db, current, ls.load_balancer_id)


@app.put("/pools/{pool_ref}")
def update_pool(pool_ref: str, payload: PoolUpdate, current: CurrentUser, db: DB):
    pool, lb = _own_pool(db, current, pool_ref)
    pool.lb_algorithm = payload.lb_algorithm
    db.commit()
    _reapply(db, lb)
    return _pool_to_dict(pool)


@app.delete("/pools/{pool_ref}", status_code=204)
def delete_pool(pool_ref: str, current: CurrentUser, db: DB):
    pool, lb = _own_pool(db, current, pool_ref)
    if db.query(L7Policy).filter_by(redirect_pool_id=pool.id).first():
        raise HTTPException(status_code=409, detail="pool is the target of an L7 policy")
    db.query(Listener).filter_by(default_pool_id=pool.id).update({"default_pool_id": None})
    db.query(Member).filter_by(pool_id=pool.id).delete()
    db.query(HealthMonitor).filter_by(pool_id=pool.id).delete()
    db.delete(pool)
    db.commit()
    _reapply(db, lb)


@app.delete("/pools/{pool_ref}/health-monitors", status_code=204)
def delete_health_monitor(pool_ref: str, current: CurrentUser, db: DB):
    pool, lb = _own_pool(db, current, pool_ref)
    if not db.query(HealthMonitor).filter_by(pool_id=pool.id).delete():
        raise HTTPException(status_code=404, detail="pool has no health monitor")
    db.commit()
    _reapply(db, lb)


@app.delete("/listeners/{ls_ref}", status_code=204)
def delete_listener(ls_ref: str, current: CurrentUser, db: DB):
    ls, lb = _own_listener(db, current, ls_ref)
    if db.query(Pool).filter_by(listener_id=ls.id).first():
        raise HTTPException(status_code=409, detail="delete the listener's pools first")
    pol_ids = db.query(L7Policy.id).filter_by(listener_id=ls.id)
    db.query(L7Rule).filter(L7Rule.policy_id.in_(pol_ids)).delete(synchronize_session=False)
    db.query(L7Policy).filter_by(listener_id=ls.id).delete()
    db.delete(ls)
    db.commit()
    _reapply(db, lb)


@app.post("/listeners/{ls_ref}/l7policies", status_code=201)
def create_l7policy(ls_ref: str, payload: L7PolicyIn, current: CurrentUser, db: DB):
    ls, lb = _own_listener(db, current, ls_ref)
    if lb.lb_type != "ALB" or ls.protocol not in ("HTTP", "HTTPS"):
        raise HTTPException(status_code=400, detail="L7 policies need an ALB with an HTTP/HTTPS listener")
    if payload.action == "REDIRECT_TO_POOL":
        pool = payload.redirect_pool_id and db.query(Pool).filter_by(id=payload.redirect_pool_id, load_balancer_id=lb.id).first()
        if not pool:
            raise HTTPException(status_code=400, detail="redirect_pool_id must be a pool of this load balancer")
    pol = L7Policy(listener_id=ls.id, name=payload.name, action=payload.action, position=payload.position,
                   redirect_pool_id=payload.redirect_pool_id if payload.action == "REDIRECT_TO_POOL" else None)
    db.add(pol)
    db.commit()
    _reapply(db, lb)
    return {"id": pol.id, "listener_id": ls.id, "name": pol.name, "action": pol.action, "position": pol.position,
            "redirect_pool_id": pol.redirect_pool_id}


def _policy(db, current, pol_ref: str):
    pol = db.query(L7Policy).filter_by(id=pol_ref).first()
    if not pol:
        raise HTTPException(status_code=404, detail="l7 policy not found")
    ls, lb = _own_listener(db, current, pol.listener_id)
    return pol, lb


@app.get("/listeners/{ls_ref}/l7policies")
def list_l7policies(ls_ref: str, current: CurrentUser, db: DB):
    ls, _ = _own_listener(db, current, ls_ref)
    out = []
    for p in db.query(L7Policy).filter_by(listener_id=ls.id).order_by(L7Policy.position):
        rules = [{"id": r.id, "type": r.type, "compare_type": r.compare_type, "value": r.value}
                 for r in db.query(L7Rule).filter_by(policy_id=p.id)]
        out.append({"id": p.id, "name": p.name, "action": p.action, "position": p.position,
                    "redirect_pool_id": p.redirect_pool_id, "rules": rules})
    return {"l7policies": out}


@app.delete("/l7policies/{pol_ref}", status_code=204)
def delete_l7policy(pol_ref: str, current: CurrentUser, db: DB):
    pol, lb = _policy(db, current, pol_ref)
    db.query(L7Rule).filter_by(policy_id=pol.id).delete()
    db.delete(pol)
    db.commit()
    _reapply(db, lb)


@app.post("/l7policies/{pol_ref}/rules", status_code=201)
def create_l7rule(pol_ref: str, payload: L7RuleIn, current: CurrentUser, db: DB):
    pol, lb = _policy(db, current, pol_ref)
    rule = L7Rule(policy_id=pol.id, type=payload.type, compare_type=payload.compare_type, value=payload.value)
    db.add(rule)
    db.commit()
    _reapply(db, lb)
    return {"id": rule.id, "policy_id": pol.id, "type": rule.type, "compare_type": rule.compare_type, "value": rule.value}


@app.delete("/l7policies/{pol_ref}/rules/{rule_id}", status_code=204)
def delete_l7rule(pol_ref: str, rule_id: str, current: CurrentUser, db: DB):
    pol, lb = _policy(db, current, pol_ref)
    if not db.query(L7Rule).filter_by(id=rule_id, policy_id=pol.id).delete():
        raise HTTPException(status_code=404, detail="l7 rule not found")
    db.commit()
    _reapply(db, lb)


# ═══════════════════════════════════════════════════════════════════════════════
# lb-worker (runs haproxy for ALBs on a compute node) -- internal
# ═══════════════════════════════════════════════════════════════════════════════

class MemberStatusIn(BaseModel):
    statuses: dict[str, str]  # member id -> ONLINE | OFFLINE


@app.get("/internal/lb-agent/{host}", dependencies=[InternalAuth])
def lb_agent_state(host: str, db: DB):
    """Everything lb-worker on `host` must be running: one entry per ALB placed there."""
    out = []
    for lb in db.query(LoadBalancer).filter_by(lb_type="ALB", host=host):
        if lb.provisioning_status == "ERROR" and not lb.vip_address:
            continue
        out.append({"lb": _lb_to_dict(lb), "listeners": lb_state(db, lb, with_pem=True)})
    return {"load_balancers": out}


@app.post("/internal/lb-agent/{host}/members", dependencies=[InternalAuth])
def lb_agent_report(host: str, payload: MemberStatusIn, db: DB):
    for mid, st in payload.statuses.items():
        if st in ("ONLINE", "OFFLINE"):
            db.query(Member).filter_by(id=mid).update({"operating_status": st})
    db.commit()
    return {"updated": len(payload.statuses)}

# ═══════════════════════════════════════════════════════════════════════════════
# Health
# ═══════════════════════════════════════════════════════════════════════════════

try:
    from shared.metrics import setup_metrics
    setup_metrics(app, "load-balancer-api")
except Exception:  # best-effort: /metrics is optional, the API must still start
    pass

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
