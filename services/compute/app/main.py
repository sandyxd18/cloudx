import base64
import hashlib
import logging
import os
import shutil
import sys
import uuid
from datetime import datetime
from typing import Annotated

import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, DateTime, Integer, String, create_engine, inspect, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

try:
    from app.auth_middleware import CurrentUser, AdminUser, OptionalAuth
except ImportError:
    from auth_middleware import CurrentUser, AdminUser, OptionalAuth

# Import shared request_id_middleware
try:
    from shared.request_id_middleware import RequestIDMiddleware
except ImportError:
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
        from shared.request_id_middleware import RequestIDMiddleware
    except ImportError:
        RequestIDMiddleware = None

# Import shared RPC library
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
try:
    from shared.rpc import RPCClient
except ImportError:
    from rpc import RPCClient  # fallback for local dev

log = logging.getLogger("compute-api")

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://mini:mini@postgres:5432/mini_openstack")
IMAGE_API_URL = os.getenv("IMAGE_API_URL", "http://image-api:8000").rstrip("/")
NETWORKING_API_URL = os.getenv("NETWORKING_API_URL", "http://networking-api:8000").rstrip("/")
BLOCK_STORE_API_URL = os.getenv("BLOCK_STORE_API_URL", "http://block-store-api:8000").rstrip("/")
AUTH_API_URL = os.getenv("AUTH_API_URL", "http://auth-api:8000").rstrip("/")
AUTH_ADMIN_USERNAME = os.getenv("AUTH_ADMIN_USERNAME", "admin")
AUTH_ADMIN_PASSWORD = os.getenv("AUTH_ADMIN_PASSWORD", "admin123")
COMPUTE_BACKEND = os.getenv("COMPUTE_BACKEND", "libvirt").lower()
DEFAULT_VM_USER = os.getenv("DEFAULT_VM_USER", "ubuntu")
NOVNC_BASE_URL = os.getenv("NOVNC_BASE_URL", "http://localhost:6080/vnc.html")
RPC_CALL_TIMEOUT = int(os.getenv("RPC_CALL_TIMEOUT", "30"))
ONLY_BACKEND = "libvirt"

# ── Internal Service Token ────────────────────────────────────────────────────
# JWT obtained at startup for service-to-service calls (networking, image, block-store).
_svc_token: str | None = None


def _get_svc_token() -> str | None:
    """Return a cached service token, refreshing if None."""
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
            log.info("compute-api: service token obtained")
        else:
            log.warning("compute-api: failed to obtain service token: HTTP %s", resp.status_code)
    except Exception as exc:
        log.warning("compute-api: could not reach auth-api for service token: %s", exc)
    return _svc_token


def _svc_headers() -> dict:
    token = _get_svc_token()
    if token:
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    return {"Content-Type": "application/json"}

# ── RPC Client (initialized on startup) ──────────────────────────────────────
_rpc: RPCClient | None = None

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def new_id() -> str:
    return str(uuid.uuid4())


def now() -> datetime:
    return datetime.utcnow()


class Server(Base):
    __tablename__ = "compute_servers"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String, unique=True, index=True)
    project_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    image_id: Mapped[str] = mapped_column(String)
    root_volume_id: Mapped[str | None] = mapped_column(String, nullable=True)
    boot_source: Mapped[str | None] = mapped_column(String, nullable=True)
    bootable: Mapped[bool] = mapped_column(Boolean, default=True)
    flavor: Mapped[str] = mapped_column(String)
    flavor_name: Mapped[str | None] = mapped_column(String, nullable=True)
    vcpus: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ram_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    disk_gb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    network_id: Mapped[str] = mapped_column(String)
    port_id: Mapped[str] = mapped_column(String)
    extra_port_ids: Mapped[str | None] = mapped_column(String, nullable=True)
    hostname: Mapped[str | None] = mapped_column(String, nullable=True)
    user_data: Mapped[str | None] = mapped_column(String, nullable=True)
    vendor_data: Mapped[str | None] = mapped_column(String, nullable=True)
    keypair_name: Mapped[str | None] = mapped_column(String, nullable=True)
    public_key_snapshot: Mapped[str | None] = mapped_column(String, nullable=True)
    server_group_id: Mapped[str | None] = mapped_column(String, nullable=True)
    backend: Mapped[str] = mapped_column(String, default=ONLY_BACKEND)
    host: Mapped[str | None] = mapped_column(String, nullable=True)
    instance_dir: Mapped[str | None] = mapped_column(String, nullable=True)
    disk_path: Mapped[str | None] = mapped_column(String, nullable=True)
    config_drive_path: Mapped[str | None] = mapped_column(String, nullable=True)
    libvirt_domain_name: Mapped[str | None] = mapped_column(String, nullable=True)
    tap_name: Mapped[str | None] = mapped_column(String, nullable=True)
    mac_address: Mapped[str | None] = mapped_column(String, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="BUILD")
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    locked_by: Mapped[str | None] = mapped_column(String, nullable=True)
    shelved_image_id: Mapped[str | None] = mapped_column(String, nullable=True)
    original_flavor: Mapped[str | None] = mapped_column(String, nullable=True)   # flavor id before resize
    original_vcpus: Mapped[int | None] = mapped_column(Integer, nullable=True)
    original_ram_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class ServerTag(Base):
    __tablename__ = "compute_server_tags"
    server_id: Mapped[str] = mapped_column(String, primary_key=True)
    tag: Mapped[str] = mapped_column(String, primary_key=True)


class Flavor(Base):
    __tablename__ = "compute_flavors"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String, unique=True, index=True)
    vcpus: Mapped[int] = mapped_column(Integer)
    ram_mb: Mapped[int] = mapped_column(Integer)
    disk_gb: Mapped[int] = mapped_column(Integer)
    ephemeral_gb: Mapped[int] = mapped_column(Integer, default=0)
    swap_mb: Mapped[int] = mapped_column(Integer, default=0)
    is_public: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class ServerGroup(Base):
    __tablename__ = "compute_server_groups"
    id:         Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name:       Mapped[str] = mapped_column(String, index=True)
    policy:     Mapped[str] = mapped_column(String, default="anti-affinity")  # anti-affinity | affinity | soft-anti-affinity
    project_id: Mapped[str | None] = mapped_column(String, nullable=True)
    members:    Mapped[str | None] = mapped_column(String, nullable=True)  # comma-separated server IDs
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Keypair(Base):
    __tablename__ = "compute_keypairs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String, unique=True, index=True)
    public_key: Mapped[str] = mapped_column(String)
    fingerprint: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class ServerIn(BaseModel):
    name: str
    image: str | None = None
    boot_volume: str | None = None
    flavor: str
    network: str
    networks: list[str] = Field(default_factory=list)
    key_name: str | None = None
    backend: str | None = None
    user_data: str | None = None
    security_groups: list[str] = Field(default_factory=list)
    server_group: str | None = None   # server group id or name
    volume_size: int | None = None    # root volume size override (GB)


class ServerResizeIn(BaseModel):
    flavor: str


class ServerRebuildIn(BaseModel):
    image: str


class ServerRescueIn(BaseModel):
    image: str | None = None


class ServerPasswordIn(BaseModel):
    password: str


class ServerRebootIn(BaseModel):
    type: str = "soft"  # soft | hard


class ServerGroupIn(BaseModel):
    name: str
    policy: str = "anti-affinity"


class ServerUserDataIn(BaseModel):
    user_data: str


class FlavorIn(BaseModel):
    name: str
    vcpus: int = Field(gt=0)
    ram_mb: int = Field(gt=0)
    disk_gb: int = Field(gt=0)
    ephemeral_gb: int = Field(default=0, ge=0)
    swap_mb: int = Field(default=0, ge=0)
    is_public: bool = True


class KeypairIn(BaseModel):
    name: str


class KeypairImportIn(BaseModel):
    name: str
    public_key: str


class VolumeAttachIn(BaseModel):
    volume_id: str
    volume_name: str | None = None
    path: str
    target_dev: str | None = None


class ServerSnapshotIn(BaseModel):
    name: str


class ServerPortIn(BaseModel):
    port: str


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


DB = Annotated[Session, Depends(get_db)]
app = FastAPI(title="cloudx compute-api", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
if RequestIDMiddleware:
    app.add_middleware(RequestIDMiddleware)


@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)
    ensure_columns()
    bootstrap_flavors()


def ensure_columns():
    inspector = inspect(engine)
    table_names = inspector.get_table_names()
    if "compute_servers" in table_names:
        columns = {column["name"] for column in inspector.get_columns("compute_servers")}
        with engine.begin() as conn:
            if "keypair_name" not in columns:
                conn.execute(text("ALTER TABLE compute_servers ADD COLUMN keypair_name VARCHAR"))
            if "public_key_snapshot" not in columns:
                conn.execute(text("ALTER TABLE compute_servers ADD COLUMN public_key_snapshot VARCHAR"))
            string_columns = (
                "backend",
                "host",
                "instance_dir",
                "disk_path",
                "config_drive_path",
                "libvirt_domain_name",
                "tap_name",
                "mac_address",
                "ip_address",
                "error_message",
                "flavor_name",
                "hostname",
                "user_data",
                "vendor_data",
                "root_volume_id",
                "boot_source",
                "extra_port_ids",
                "project_id",
                "server_group_id",
                "locked_by",
            )
            for column in string_columns:
                if column not in columns:
                    default = " DEFAULT 'libvirt'" if column == "backend" else ""
                    conn.execute(text(f"ALTER TABLE compute_servers ADD COLUMN {column} VARCHAR{default}"))
            for column in ("vcpus", "ram_mb", "disk_gb"):
                if column not in columns:
                    conn.execute(text(f"ALTER TABLE compute_servers ADD COLUMN {column} INTEGER"))
            if "bootable" not in columns:
                conn.execute(text("ALTER TABLE compute_servers ADD COLUMN bootable BOOLEAN DEFAULT TRUE"))
            if "locked" not in columns:
                conn.execute(text("ALTER TABLE compute_servers ADD COLUMN locked BOOLEAN DEFAULT FALSE"))
            if "shelved_image_id" not in columns:
                conn.execute(text("ALTER TABLE compute_servers ADD COLUMN shelved_image_id VARCHAR"))
            if "original_flavor" not in columns:
                conn.execute(text("ALTER TABLE compute_servers ADD COLUMN original_flavor VARCHAR"))
            if "original_vcpus" not in columns:
                conn.execute(text("ALTER TABLE compute_servers ADD COLUMN original_vcpus INTEGER"))
            if "original_ram_mb" not in columns:
                conn.execute(text("ALTER TABLE compute_servers ADD COLUMN original_ram_mb INTEGER"))
            conn.execute(text("UPDATE compute_servers SET backend = 'libvirt' WHERE backend IS NULL OR backend = 'simulator'"))
            conn.execute(text("UPDATE compute_servers SET flavor_name = flavor WHERE flavor_name IS NULL"))
            conn.execute(text("UPDATE compute_servers SET locked = FALSE WHERE locked IS NULL"))

    if "compute_flavors" in table_names:
        flavor_columns = {column["name"] for column in inspector.get_columns("compute_flavors")}
        with engine.begin() as conn:
            for column, ddl in (
                ("ephemeral_gb", "INTEGER DEFAULT 0"),
                ("swap_mb", "INTEGER DEFAULT 0"),
                ("is_public", "BOOLEAN DEFAULT TRUE"),
                ("created_at", "TIMESTAMP"),
                ("updated_at", "TIMESTAMP"),
            ):
                if column not in flavor_columns:
                    conn.execute(text(f"ALTER TABLE compute_flavors ADD COLUMN {column} {ddl}"))


def bootstrap_flavors():
    defaults = [
        {"name": "tiny", "vcpus": 1, "ram_mb": 512, "disk_gb": 5},
        {"name": "small", "vcpus": 1, "ram_mb": 1024, "disk_gb": 10},
        {"name": "medium", "vcpus": 2, "ram_mb": 2048, "disk_gb": 20},
    ]
    db = SessionLocal()
    try:
        for item in defaults:
            if not get_flavor(db, item["name"]):
                db.add(Flavor(**item, ephemeral_gb=0, swap_mb=0, is_public=True))
        db.commit()
    finally:
        db.close()


def obj_to_dict(obj):
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}


def server_to_dict(server: Server) -> dict:
    data = obj_to_dict(server)
    data["metadata_url_debug"] = f"http://localhost:8775/openstack/latest/meta_data.json?instance_id={server.id}"
    return data


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


def public_key_fingerprint(public_key: str) -> str:
    parts = public_key.strip().split()
    if len(parts) < 2:
        raise HTTPException(status_code=400, detail="public key must be in OpenSSH authorized_keys format")
    try:
        key_blob = base64.b64decode(parts[1].encode("ascii"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail="public key base64 payload is invalid") from exc
    digest = base64.b64encode(hashlib.sha256(key_blob).digest()).decode("ascii").rstrip("=")
    return f"SHA256:{digest}"


def generate_keypair_material() -> tuple[str, str]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    ).decode("ascii")
    return private_pem, public_key


def api_get(url: str):
    try:
        response = requests.get(url, headers=_svc_headers(), timeout=8)
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if response.status_code == 401:
        # Token may have expired — clear and retry once with fresh token
        global _svc_token
        _svc_token = None
        fresh_headers = _svc_headers()
        try:
            response = requests.get(url, headers=fresh_headers, timeout=8)
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=response_detail(response))
    return response.json()


def api_post(url: str, payload: dict):
    try:
        response = requests.post(url, json=payload, headers=_svc_headers(), timeout=300)
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if response.status_code == 401:
        global _svc_token
        _svc_token = None
        try:
            response = requests.post(url, json=payload, headers=_svc_headers(), timeout=300)
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=response_detail(response))
    return response.json()


def api_delete(url: str, payload: dict | None = None):
    try:
        resp = requests.delete(url, json=payload, headers=_svc_headers(), timeout=20)
        if resp.status_code == 401:
            global _svc_token
            _svc_token = None
            requests.delete(url, json=payload, headers=_svc_headers(), timeout=20)
    except requests.RequestException:
        pass


def response_detail(response: requests.Response):
    try:
        return response.json().get("detail", response.text)
    except ValueError:
        return response.text


# ── RPC helpers (replaces worker_post / worker_delete) ────────────────────────

def cast_to_worker(method: str, payload: dict) -> None:
    """Fire-and-forget to compute worker via RabbitMQ (async)."""
    _rpc.cast("compute", method, payload)
    log.info("CAST compute.%s for server %s", method, payload.get("id", "?"))


def call_worker(method: str, payload: dict, timeout: int | None = None) -> dict:
    """Synchronous RPC call — waits for worker reply."""
    return _rpc.call("compute", method, payload, timeout=timeout or RPC_CALL_TIMEOUT)


def release_server_volumes(server_id: str) -> dict | None:
    try:
        response = requests.post(f"{BLOCK_STORE_API_URL}/servers/{server_id}/volumes/detach-all", timeout=20)
    except requests.RequestException:
        return None
    if response.status_code >= 400:
        return None
    return response.json()


def block_get(path: str) -> dict:
    return api_get(f"{BLOCK_STORE_API_URL}{path}")


def block_post(path: str, payload: dict) -> dict:
    return api_post(f"{BLOCK_STORE_API_URL}{path}", payload)


def block_delete(path: str) -> None:
    api_delete(f"{BLOCK_STORE_API_URL}{path}")


def comma_list(value: str | None) -> list[str]:
    return [item for item in (value or "").split(",") if item]


def format_comma_list(values: list[str]) -> str | None:
    return ",".join(dict.fromkeys([item for item in values if item])) or None


def placement_inventory(db: Session) -> dict:
    active = db.scalars(select(Server).where(Server.status.in_({"BUILD", "ACTIVE", "SHUTOFF", "REBOOT"}))).all()
    default_disk = shutil.disk_usage("/").total // (1024 ** 3)
    total_vcpu = int(os.getenv("PLACEMENT_TOTAL_VCPU", str(os.cpu_count() or 1)))
    total_ram = int(os.getenv("PLACEMENT_TOTAL_RAM_MB", "8192"))
    total_disk = int(os.getenv("PLACEMENT_TOTAL_DISK_GB", str(default_disk)))
    used_vcpu = sum(server.vcpus or 0 for server in active)
    used_ram = sum(server.ram_mb or 0 for server in active)
    used_disk = sum(server.disk_gb or 0 for server in active)
    return {
        "total_vcpu": total_vcpu,
        "used_vcpu": used_vcpu,
        "free_vcpu": max(total_vcpu - used_vcpu, 0),
        "total_ram_mb": total_ram,
        "used_ram_mb": used_ram,
        "free_ram_mb": max(total_ram - used_ram, 0),
        "total_disk_gb": total_disk,
        "used_disk_gb": used_disk,
        "free_disk_gb": max(total_disk - used_disk, 0),
    }


def ensure_capacity(db: Session, flavor: Flavor) -> None:
    inv = placement_inventory(db)
    if flavor.vcpus > inv["free_vcpu"] or flavor.ram_mb > inv["free_ram_mb"] or flavor.disk_gb > inv["free_disk_gb"]:
        raise HTTPException(status_code=409, detail={"message": "Insufficient resources", "inventory": inv})


def selected_backend(requested: str | None) -> str:
    backend = (requested or COMPUTE_BACKEND or ONLY_BACKEND).lower()
    if backend == "simulator":
        raise HTTPException(status_code=400, detail="simulator backend has been removed; only libvirt is supported")
    if backend != ONLY_BACKEND:
        raise HTTPException(status_code=400, detail=f"unsupported compute backend '{backend}'; only libvirt is supported")
    return ONLY_BACKEND


def flavor_snapshot(flavor: Flavor) -> dict:
    return {
        "flavor_name": flavor.name,
        "vcpus": flavor.vcpus,
        "ram_mb": flavor.ram_mb,
        "disk_gb": flavor.disk_gb,
        "ephemeral_gb": flavor.ephemeral_gb,
        "swap_mb": flavor.swap_mb,
        "is_public": flavor.is_public,
    }


def server_worker_payload(server: Server, port: dict | None = None, ports: list[dict] | None = None, root_volume: dict | None = None) -> dict:
    payload = obj_to_dict(server)
    payload["flavor_snapshot"] = {
        "name": server.flavor_name or server.flavor,
        "vcpus": server.vcpus,
        "ram_mb": server.ram_mb,
        "disk_gb": server.disk_gb,
    }
    if port:
        payload["port"] = port
    if ports:
        payload["ports"] = ports
    if root_volume:
        payload["root_volume"] = root_volume
    return jsonable_encoder(payload)


def update_server_from_worker(server: Server, result: dict) -> None:
    for field in (
        "host",
        "instance_dir",
        "disk_path",
        "config_drive_path",
        "libvirt_domain_name",
        "tap_name",
        "mac_address",
        "ip_address",
    ):
        if field in result:
            setattr(server, field, result[field])


def default_user_data(server: Server) -> str:
    lines = ["#cloud-config", "package_update: false", "ssh_pwauth: false"]
    if server.public_key_snapshot:
        lines.extend(["ssh_authorized_keys:", f"  - {server.public_key_snapshot}"])
    return "\n".join(lines) + "\n"


def server_metadata_payload(server: Server) -> dict:
    public_keys = {}
    if server.keypair_name and server.public_key_snapshot:
        public_keys[server.keypair_name] = server.public_key_snapshot
    try:
        port = api_get(f"{NETWORKING_API_URL}/ports/{server.port_id}")
    except Exception:
        port = {}
    return {
        "uuid": server.id,
        "hostname": server.hostname or server.name,
        "name": server.name,
        "public_keys": public_keys,
        "meta": {},
        "devices": [],
        "instance_id": server.id,
        "local_hostname": server.hostname or server.name,
        "instance_type": server.flavor_name or server.flavor,
        "port_id": server.port_id,
        "fixed_ip": server.ip_address,
        "network_id": server.network_id,
        "mac_address": server.mac_address,
        "created_at": server.created_at.isoformat() if server.created_at else None,
        "user_data": server.user_data,
        "vendor_data": server.vendor_data or "{}",
        "network": port,
    }


@app.on_event("startup")
def _startup_rpc():
    global _rpc
    _rpc = RPCClient()
    log.info("RPC client initialized")


@app.on_event("shutdown")
def _shutdown_rpc():
    if _rpc:
        _rpc.close()


@app.get("/health")
def health():
    return {"status": "ok", "service": "compute-api", "default_backend": ONLY_BACKEND}


@app.get("/host/check")
def host_check(_: CurrentUser):
    try:
        return call_worker("health", {}, timeout=5)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Worker unreachable: {exc}") from exc


@app.get("/host")
def host_show(_: CurrentUser, db: DB):
    try:
        worker = call_worker("health", {}, timeout=5)
    except Exception:
        worker = {"status": "unreachable"}
    return {"host": "single-node", "worker": worker, "placement": placement_inventory(db)}


@app.post("/flavors", status_code=201)
def create_flavor(payload: FlavorIn, _: CurrentUser, db: DB):
    if get_flavor(db, payload.name):
        raise HTTPException(status_code=409, detail="flavor already exists")
    flavor = Flavor(**payload.model_dump())
    db.add(flavor)
    db.commit()
    db.refresh(flavor)
    return obj_to_dict(flavor)


@app.get("/flavors")
def list_flavors(_: CurrentUser, db: DB):
    return {"items": [obj_to_dict(f) for f in db.scalars(select(Flavor).order_by(Flavor.created_at)).all()]}


@app.get("/flavors/{ref}")
def show_flavor(ref: str, _: CurrentUser, db: DB):
    return obj_to_dict(require_flavor(db, ref))


@app.delete("/flavors/{ref}")
def delete_flavor(ref: str, _: AdminUser, db: DB):
    flavor = require_flavor(db, ref)
    db.delete(flavor)
    db.commit()
    return {"status": "deleted", "id": flavor.id, "name": flavor.name}


# ── Server Groups ─────────────────────────────────────────────────────────────
@app.post("/server-groups", status_code=201)
def create_server_group(payload: ServerGroupIn, current: CurrentUser, db: DB):
    sg = ServerGroup(
        name=payload.name,
        policy=payload.policy,
        project_id=current["project_id"],
    )
    db.add(sg)
    db.commit()
    db.refresh(sg)
    return {**obj_to_dict(sg), "members": []}


@app.get("/server-groups")
def list_server_groups(current: CurrentUser, db: DB):
    q = select(ServerGroup).where(ServerGroup.project_id == current["project_id"])
    groups = db.scalars(q).all()
    return {"items": [{**obj_to_dict(g), "members": [m for m in (g.members or "").split(",") if m]} for g in groups]}


@app.get("/server-groups/{ref}")
def show_server_group(ref: str, current: CurrentUser, db: DB):
    sg = db.get(ServerGroup, ref) or db.scalar(
        select(ServerGroup).where(ServerGroup.name == ref, ServerGroup.project_id == current["project_id"]))
    if not sg:
        raise HTTPException(status_code=404, detail="server group not found")
    return {**obj_to_dict(sg), "members": [m for m in (sg.members or "").split(",") if m]}


@app.delete("/server-groups/{ref}", status_code=204)
def delete_server_group(ref: str, current: CurrentUser, db: DB):
    sg = db.get(ServerGroup, ref) or db.scalar(
        select(ServerGroup).where(ServerGroup.name == ref, ServerGroup.project_id == current["project_id"]))
    if not sg:
        raise HTTPException(status_code=404, detail="server group not found")
    db.delete(sg)
    db.commit()


@app.post("/servers", status_code=201)
def create_server(payload: ServerIn, current: CurrentUser, db: DB):
    if get_active_server_by_name(db, payload.name):
        raise HTTPException(status_code=409, detail="server already exists")
    if bool(payload.image) == bool(payload.boot_volume):
        raise HTTPException(status_code=400, detail="create server requires exactly one boot source: --image or --boot-volume")
    free_deleted_server_name(db, payload.name)
    backend = selected_backend(payload.backend)
    keypair = require_keypair(db, payload.key_name) if payload.key_name else None
    flavor = require_flavor(db, payload.flavor)
    ensure_capacity(db, flavor)
    # Check server group anti-affinity
    sg_id = None
    if payload.server_group:
        sg = db.get(ServerGroup, payload.server_group) or db.scalar(
            select(ServerGroup).where(ServerGroup.name == payload.server_group,
                                       ServerGroup.project_id == current["project_id"]))
        if not sg:
            raise HTTPException(status_code=404, detail="server group not found")
        sg_id = sg.id
        if sg.policy == "anti-affinity":
            members = [m for m in (sg.members or "").split(",") if m]
            if len(members) >= 1:
                raise HTTPException(status_code=409,
                    detail=f"Server group '{sg.name}' anti-affinity policy violated: {len(members)} member(s) already exist")
    network = api_get(f"{NETWORKING_API_URL}/networks/{payload.network}")
    server_id = new_id()
    root_volume = None
    auto_root_volume = False
    image_id = ""
    boot_source = "volume"
    created_ports: list[dict] = []
    project_id = current["project_id"]
    try:
        if payload.image:
            image = api_get(f"{IMAGE_API_URL}/images/{payload.image}")
            image_id = image["id"]
            root_volume = block_post(
                "/internal/volumes",
                {
                    "name": f"{payload.name}-root-{server_id[:8]}",
                    "size_gb": flavor.disk_gb,
                    "image_id": image["id"],
                    "image_ref": image["id"],
                    "bootable": True,
                    "source_type": "image",
                },
            )
            boot_source = "image"
            auto_root_volume = True
        else:
            root_volume = block_get(f"/volumes/{payload.boot_volume}")
            if not root_volume.get("bootable"):
                raise HTTPException(status_code=400, detail="boot volume must be bootable=true")
            if root_volume.get("status") != "available":
                raise HTTPException(status_code=409, detail=f"boot volume must be available; current status is {root_volume.get('status')}")
            image_id = root_volume.get("image_id") or ""
            boot_source = "volume"

        network_refs = [payload.network, *payload.networks]
        seen_networks = []
        for item in network_refs:
            if item and item not in seen_networks:
                seen_networks.append(item)
        ports = []
        for index, network_ref in enumerate(seen_networks):
            net = network if index == 0 else api_get(f"{NETWORKING_API_URL}/networks/{network_ref}")
            port = api_post(
                f"{NETWORKING_API_URL}/ports",
                {
                    "name": server_port_name(payload.name, server_id) if index == 0 else f"{payload.name}-{server_id[:8]}-port{index}",
                    "network": net["id"],
                    "device_id": server_id,
                    "device_owner": "compute:nova-like",
                    "security_group_ids": payload.security_groups,
                },
            )
            ports.append(port)
            created_ports.append(port)
        port = ports[0]
        snapshot = flavor_snapshot(flavor)
        server = Server(
            id=server_id,
            name=payload.name,
            image_id=image_id,
            root_volume_id=root_volume["id"],
            boot_source=boot_source,
            bootable=True,
            flavor=flavor.name,
            flavor_name=snapshot["flavor_name"],
            vcpus=snapshot["vcpus"],
            ram_mb=snapshot["ram_mb"],
            disk_gb=snapshot["disk_gb"],
            network_id=network["id"],
            port_id=port["id"],
            extra_port_ids=format_comma_list([p["id"] for p in ports[1:]]),
            hostname=payload.name,
            user_data=payload.user_data,
            vendor_data="{}",
            keypair_name=keypair.name if keypair else None,
            public_key_snapshot=keypair.public_key if keypair else None,
            backend=backend,
            mac_address=port.get("mac_address"),
            ip_address=port.get("fixed_ip") or port.get("ip_address"),
            status="BUILD",
        )
        db.add(server)
        db.commit()
        db.refresh(server)
        # ── Async via RabbitMQ ──────────────────────────────────────────────
        # Cast build_instance to compute-worker queue. Worker will:
        #   1. Spawn VM (libvirt)
        #   2. Call conductor to update server fields (host, mac, etc.)
        #   3. Call block-store to attach root volume
        #   4. Call conductor to set status ACTIVE
        worker_payload = server_worker_payload(server, port, ports, root_volume)
        worker_payload["_root_volume_id"] = root_volume["id"]
        worker_payload["_block_store_url"] = BLOCK_STORE_API_URL
        cast_to_worker("build_instance", worker_payload)
        # API returns immediately with status=BUILD
    except Exception as exc:
        for created in created_ports:
            api_delete(f"{NETWORKING_API_URL}/ports/{created['id']}")
        if auto_root_volume and root_volume:
            block_delete(f"/volumes/{root_volume['id']}")
        existing = db.get(Server, server_id)
        if existing:
            existing.status = "ERROR"
            existing.error_message = str(exc)
            db.commit()
            db.refresh(existing)
            return obj_to_dict(existing)
        raise
    db.commit()
    db.refresh(server)
    return server_to_dict(server)


@app.get("/servers")
def list_servers(
    current: CurrentUser, db: DB,
    limit: int = Query(default=200, ge=1, le=1000),
    marker: str | None = None,
    tags: str | None = None,
):
    q = select(Server).where(Server.status != "DELETED")
    if current.get("role") != "admin":
        q = q.where(Server.project_id == current["project_id"])
    if tags:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        for tag in tag_list:
            tagged_server_ids = db.scalars(
                select(ServerTag.server_id).where(ServerTag.tag == tag)
            ).all()
            q = q.where(Server.id.in_(tagged_server_ids))
    if marker:
        ref = db.get(Server, marker)
        if ref:
            q = q.where(Server.created_at > ref.created_at)
    q = q.order_by(Server.created_at).limit(limit)
    return {"items": [server_to_dict(s) for s in db.scalars(q).all()]}


@app.get("/servers/metadata-lookup")
def metadata_lookup(
    db: DB,
    instance_id: str | None = None,
    fixed_ip: str | None = None,
    port_id: str | None = None,
):
    query = select(Server).where(Server.status != "DELETED")
    if instance_id:
        query = query.where(Server.id == instance_id)
    elif fixed_ip:
        query = query.where(Server.ip_address == fixed_ip)
    elif port_id:
        query = query.where(Server.port_id == port_id)
    else:
        raise HTTPException(status_code=400, detail="instance_id, fixed_ip, or port_id is required")
    # Order by most recently created to ensure deterministic results when multiple
    # servers share the same IP (e.g. after repeated lab cleanup/create cycles).
    query = query.order_by(Server.created_at.desc())
    server = db.scalar(query)
    if not server:
        raise HTTPException(status_code=404, detail="server metadata not found")
    return server_metadata_payload(server)


@app.get("/internal/servers/by-instance/{instance_id}")
def internal_server_by_instance(instance_id: str, db: DB):
    server = db.scalar(select(Server).where(Server.id == instance_id, Server.status != "DELETED").order_by(Server.created_at.desc()))
    if not server:
        raise HTTPException(status_code=404, detail="server metadata not found")
    return server_metadata_payload(server)


@app.get("/internal/servers/by-port/{port_id}")
def internal_server_by_port(port_id: str, db: DB):
    server = db.scalar(select(Server).where(Server.port_id == port_id, Server.status != "DELETED").order_by(Server.created_at.desc()))
    if not server:
        raise HTTPException(status_code=404, detail="server metadata not found")
    return server_metadata_payload(server)


@app.get("/internal/servers/by-ip/{fixed_ip}")
def internal_server_by_ip(fixed_ip: str, db: DB):
    server = db.scalar(select(Server).where(Server.ip_address == fixed_ip, Server.status != "DELETED").order_by(Server.created_at.desc()))
    if not server:
        raise HTTPException(status_code=404, detail="server metadata not found")
    return server_metadata_payload(server)


@app.get("/servers/{ref}")
def show_server(ref: str, current: CurrentUser, db: DB):
    server = require_server(db, ref)
    if current.get("role") != "admin" and server.project_id and server.project_id != current["project_id"]:
        raise HTTPException(status_code=403, detail="forbidden")
    return server_to_dict(server)


@app.get("/servers/{ref}/metadata")
def server_metadata(ref: str, db: DB):
    return server_metadata_payload(require_server(db, ref))


@app.get("/servers/{ref}/user-data")
def server_user_data(ref: str, db: DB):
    server = require_server(db, ref)
    return {"server": server.name, "user_data": server.user_data or default_user_data(server)}


@app.put("/servers/{ref}/user-data")
def set_server_user_data(ref: str, payload: ServerUserDataIn, db: DB):
    server = require_server(db, ref)
    server.user_data = payload.user_data
    db.commit()
    db.refresh(server)
    return {"server": server.name, "user_data": server.user_data}


@app.post("/servers/{ref}/start")
def start_server(ref: str, db: DB):
    server = require_server(db, ref)
    server.status = "STARTING"
    db.commit()
    cast_to_worker("start", server_worker_payload(server))
    db.refresh(server)
    return obj_to_dict(server)


@app.post("/servers/{ref}/stop")
def stop_server(ref: str, db: DB):
    server = require_server(db, ref)
    server.status = "STOPPING"
    db.commit()
    cast_to_worker("stop", server_worker_payload(server))
    db.refresh(server)
    return obj_to_dict(server)


@app.post("/servers/{ref}/reboot")
def reboot_server(ref: str, payload: ServerRebootIn = None, _: CurrentUser = None, db: DB = None):
    server = require_server(db, ref)
    rtype = payload.type if payload else "soft"
    server.status = "REBOOT"
    db.commit()
    cast_to_worker("reboot", {**server_worker_payload(server), "reboot_type": rtype})
    db.refresh(server)
    return obj_to_dict(server)


@app.post("/servers/{ref}/pause")
def pause_server(ref: str, _: CurrentUser, db: DB):
    server = require_server(db, ref)
    server.status = "PAUSING"
    db.commit()
    cast_to_worker("pause", server_worker_payload(server))
    db.refresh(server)
    return obj_to_dict(server)


@app.post("/servers/{ref}/resume")
def resume_server(ref: str, _: CurrentUser, db: DB):
    server = require_server(db, ref)
    server.status = "RESUMING"
    db.commit()
    cast_to_worker("resume", server_worker_payload(server))
    db.refresh(server)
    return obj_to_dict(server)


@app.post("/servers/{ref}/suspend")
def suspend_server(ref: str, _: CurrentUser, db: DB):
    server = require_server(db, ref)
    server.status = "SUSPENDING"
    db.commit()
    cast_to_worker("suspend", server_worker_payload(server))
    db.refresh(server)
    return obj_to_dict(server)


@app.post("/servers/{ref}/resize")
def resize_server(ref: str, payload: ServerResizeIn, _: CurrentUser, db: DB):
    server = require_server(db, ref)
    flavor = require_flavor(db, payload.flavor)
    # Snapshot original flavor for potential revert
    server.original_flavor = server.flavor
    server.original_vcpus = server.vcpus
    server.original_ram_mb = server.ram_mb
    server.flavor = flavor.id
    server.flavor_name = flavor.name
    server.vcpus = flavor.vcpus
    server.ram_mb = flavor.ram_mb
    server.disk_gb = flavor.disk_gb
    server.status = "RESIZE"
    db.commit()
    cast_to_worker("resize", {**server_worker_payload(server),
        "new_vcpus": flavor.vcpus, "new_ram_mb": flavor.ram_mb})
    db.refresh(server)
    return {**obj_to_dict(server), "new_flavor": flavor.name}


@app.post("/servers/{ref}/confirmResize")
def confirm_resize(ref: str, _: CurrentUser, db: DB):
    """Confirm a completed resize, clearing the rollback snapshot."""
    server = require_server(db, ref)
    if server.status not in ("VERIFY_RESIZE", "RESIZE", "ACTIVE"):
        raise HTTPException(status_code=409, detail=f"server is not in VERIFY_RESIZE status (current: {server.status})")
    # Clear backup state
    server.original_flavor = None
    server.original_vcpus = None
    server.original_ram_mb = None
    server.status = "ACTIVE"
    db.commit()
    db.refresh(server)
    return obj_to_dict(server)


@app.post("/servers/{ref}/revertResize")
def revert_resize(ref: str, _: CurrentUser, db: DB):
    """Revert a resize, restoring the original flavor."""
    server = require_server(db, ref)
    if not server.original_flavor:
        raise HTTPException(status_code=409, detail="no resize snapshot found for this server")
    original_flavor = db.get(Flavor, server.original_flavor)
    # Restore original flavor resources
    server.flavor = server.original_flavor
    if original_flavor:
        server.flavor_name = original_flavor.name
    server.vcpus = server.original_vcpus
    server.ram_mb = server.original_ram_mb
    server.original_flavor = None
    server.original_vcpus = None
    server.original_ram_mb = None
    server.status = "RESIZE"
    db.commit()
    # Cast revert to worker (hotplug rollback)
    cast_to_worker("resize", {**server_worker_payload(server),
        "new_vcpus": server.vcpus, "new_ram_mb": server.ram_mb})
    server.status = "ACTIVE"
    db.commit()
    db.refresh(server)
    return obj_to_dict(server)


@app.post("/servers/{ref}/rebuild")
def rebuild_server(ref: str, payload: ServerRebuildIn, _: CurrentUser, db: DB):
    server = require_server(db, ref)
    image = api_get(f"{IMAGE_API_URL}/images/{payload.image}")
    server.image_id = image["id"]
    server.status = "REBUILD"
    db.commit()
    cast_to_worker("rebuild", {**server_worker_payload(server), "image": image})
    db.refresh(server)
    return obj_to_dict(server)


@app.post("/servers/{ref}/rescue")
def rescue_server(ref: str, payload: ServerRescueIn, _: CurrentUser, db: DB):
    server = require_server(db, ref)
    rescue_image_id = server.image_id
    if payload.image:
        rescue_img = api_get(f"{IMAGE_API_URL}/images/{payload.image}")
        rescue_image_id = rescue_img["id"]
    server.status = "RESCUE"
    db.commit()
    cast_to_worker("rescue", {**server_worker_payload(server), "rescue_image_id": rescue_image_id})
    db.refresh(server)
    return obj_to_dict(server)


@app.post("/servers/{ref}/unrescue")
def unrescue_server(ref: str, _: CurrentUser, db: DB):
    server = require_server(db, ref)
    server.status = "UNRESCUING"
    db.commit()
    cast_to_worker("unrescue", server_worker_payload(server))
    db.refresh(server)
    return obj_to_dict(server)


@app.post("/servers/{ref}/password")
def set_server_password(ref: str, payload: ServerPasswordIn, _: CurrentUser, db: DB):
    server = require_server(db, ref)
    cast_to_worker("set_password", {**server_worker_payload(server), "password": payload.password})
    return {"server": server.name, "status": "password injection requested"}


@app.delete("/servers/{ref}")
def delete_server(ref: str, current: CurrentUser, db: DB, keep_root_volume: bool = False):
    server = require_server(db, ref)
    # Locked guard
    if server.locked and current.get("role") != "admin":
        raise HTTPException(status_code=409, detail="server is locked; unlock it before deleting")
    original_name = server.name
    # Remove from server group if member
    if server.server_group_id:
        sg = db.get(ServerGroup, server.server_group_id)
        if sg:
            members = [m for m in (sg.members or "").split(",") if m and m != server.id]
            sg.members = ",".join(members) or None
    server.status = "DELETING"
    db.commit()
    # Cast delete to worker — worker will:
    # 1. Destroy libvirt domain
    # 2. Cleanup ports and volumes via HTTP
    # 3. Call conductor to mark DELETED or hard-delete
    cast_to_worker("delete_instance", {
        **server_worker_payload(server),
        "_original_name": original_name,
        "_keep_root_volume": keep_root_volume,
        "_root_volume_id": server.root_volume_id,
        "_port_id": server.port_id,
        "_extra_port_ids": server.extra_port_ids,
        "_block_store_url": BLOCK_STORE_API_URL,
        "_networking_url": NETWORKING_API_URL,
    })
    return {"status": "deleting", "id": server.id, "name": original_name}


@app.post("/servers/{ref}/snapshots", status_code=201)
def create_server_snapshot(ref: str, payload: ServerSnapshotIn, db: DB):
    server = require_server(db, ref)
    if not server.root_volume_id:
        raise HTTPException(status_code=409, detail="server has no root volume")
    snap = block_post(f"/volumes/{server.root_volume_id}/snapshots", {"name": f"{payload.name}-root"})
    image = api_post(
        f"{IMAGE_API_URL}/images/import-path",
        {"name": payload.name, "path": snap["path"], "disk_format": "qcow2", "container_format": "bare", "visibility": "private"},
    )
    return {"server": server.name, "snapshot": snap, "image": image}


@app.post("/servers/{ref}/ports")
def add_server_port(ref: str, payload: ServerPortIn, db: DB):
    server = require_server(db, ref)
    if server.status != "ACTIVE":
        raise HTTPException(status_code=409, detail=f"server must be ACTIVE to add a port; current status is {server.status}")
    port = api_get(f"{NETWORKING_API_URL}/ports/{payload.port}")
    if port["id"] in [server.port_id, *comma_list(server.extra_port_ids)]:
        raise HTTPException(status_code=409, detail="port is already attached to server metadata")
    # Update DB immediately, cast hotplug to worker
    server.extra_port_ids = format_comma_list([*comma_list(server.extra_port_ids), port["id"]])
    db.commit()
    cast_to_worker("attach_port", {**server_worker_payload(server), "port": port})
    db.refresh(server)
    return {"status": "attaching", "server_id": server.id, "port_id": port["id"]}


@app.delete("/servers/{ref}/ports/{port_ref}")
def remove_server_port(ref: str, port_ref: str, db: DB):
    server = require_server(db, ref)
    extra = comma_list(server.extra_port_ids)
    port = api_get(f"{NETWORKING_API_URL}/ports/{port_ref}")
    if port["id"] not in extra:
        raise HTTPException(status_code=404, detail="port is not an attached secondary port")
    server.extra_port_ids = format_comma_list([item for item in extra if item != port["id"]])
    db.commit()
    cast_to_worker("detach_port", {**server_worker_payload(server), "port": port})
    db.refresh(server)
    return {"status": "detaching", "server_id": server.id, "port_id": port["id"]}


@app.post("/servers/{ref}/volumes/attach")
def attach_volume(ref: str, payload: VolumeAttachIn, db: DB):
    server = require_server(db, ref)
    if server.status != "ACTIVE":
        raise HTTPException(status_code=409, detail=f"server must be ACTIVE to attach volume; current status is {server.status}")
    cast_to_worker("attach_volume", {**server_worker_payload(server), "volume": payload.model_dump()})
    return {"status": "attaching", "server_id": server.id, "volume_id": payload.volume_id}


@app.post("/servers/{ref}/volumes/detach")
def detach_volume(ref: str, payload: VolumeAttachIn, db: DB):
    server = require_server(db, ref)
    if server.status not in {"ACTIVE", "SHUTOFF"}:
        raise HTTPException(status_code=409, detail=f"server must be ACTIVE or SHUTOFF to detach volume; current status is {server.status}")
    cast_to_worker("detach_volume", {**server_worker_payload(server), "volume": payload.model_dump()})
    return {"status": "detaching", "server_id": server.id, "volume_id": payload.volume_id}


@app.get("/servers/{ref}/console")
def console(ref: str, _: CurrentUser, db: DB):
    server = require_server(db, ref)
    return {
        "server": server.name,
        "domain": server.libvirt_domain_name,
        "domain_name": server.libvirt_domain_name,
        "instruction": f"virsh -c qemu:///system console {server.libvirt_domain_name}",
        "note": "VNC is auto-assigned by libvirt; use virsh dumpxml to inspect the assigned port if needed.",
    }


@app.get("/servers/{ref}/console-url")
def console_url(ref: str, _: CurrentUser, db: DB):
    server = require_server(db, ref)
    vnc = {
        "status": "auto-assigned-by-libvirt",
        "inspect": f"virsh -c qemu:///system dumpxml {server.libvirt_domain_name}",
    }
    try:
        vnc = call_worker("get_console_info", server_worker_payload(server), timeout=RPC_CALL_TIMEOUT)
    except Exception:
        pass
    novnc_url = NOVNC_BASE_URL
    if vnc.get("port"):
        novnc_url = f"{NOVNC_BASE_URL}?host=localhost&port={vnc['port']}"
    return {
        "server": server.name,
        "domain": server.libvirt_domain_name,
        "virsh_console": f"virsh -c qemu:///system console {server.libvirt_domain_name}",
        "vnc": vnc,
        "novnc_url": novnc_url,
        "note": "noVNC static UI is exposed on port 6080; VNC port is discovered from libvirt when available.",
    }


@app.post("/servers/{ref}/remote-consoles")
def remote_console(ref: str, payload: dict, _: CurrentUser, db: DB):
    """
    OpenStack-compatible remote console endpoint.
    Returns a noVNC URL for in-browser VM console access.

    Request body: {"remote_console": {"type": "novnc", "protocol": "vnc"}}
    """
    server = require_server(db, ref)
    if server.status != "ACTIVE":
        raise HTTPException(status_code=409, detail=f"server must be ACTIVE to get a console (current: {server.status})")

    vnc_info: dict = {}
    try:
        vnc_info = call_worker("get_console_info", server_worker_payload(server), timeout=RPC_CALL_TIMEOUT)
    except Exception:
        pass

    novnc_url = NOVNC_BASE_URL
    if vnc_info.get("port"):
        novnc_url = f"{NOVNC_BASE_URL}?host=localhost&port={vnc_info['port']}"

    return {
        "remote_console": {
            "type": "novnc",
            "protocol": "vnc",
            "url": novnc_url,
            "vnc_port": vnc_info.get("port"),
            "server": server.name,
        }
    }


@app.post("/servers/{ref}/shelve")
def shelve_server(ref: str, current: CurrentUser, db: DB):
    """
    Shelve a server: snapshot its state to an image, then stop and free resources.
    Status: ACTIVE/SHUTOFF → SHELVED_OFFLOADED
    """
    server = require_server(db, ref)
    if server.locked and current.get("role") != "admin":
        raise HTTPException(status_code=409, detail="server is locked")
    if server.status not in ("ACTIVE", "SHUTOFF", "PAUSED", "SUSPENDED"):
        raise HTTPException(status_code=409, detail=f"cannot shelve server in state {server.status}")
    if not server.root_volume_id:
        raise HTTPException(status_code=409, detail="shelve requires a root volume (boot-from-volume)")

    server.status = "SHELVING"
    db.commit()
    cast_to_worker("shelve", {
        **server_worker_payload(server),
        "_image_api_url": IMAGE_API_URL,
        "_block_store_url": BLOCK_STORE_API_URL,
    })
    return {"server": server.name, "status": "shelving"}


@app.post("/servers/{ref}/unshelve")
def unshelve_server(ref: str, current: CurrentUser, db: DB):
    """
    Unshelve a server: restore from shelved image and restart.
    Status: SHELVED_OFFLOADED → ACTIVE
    """
    server = require_server(db, ref)
    if server.locked and current.get("role") != "admin":
        raise HTTPException(status_code=409, detail="server is locked")
    if server.status != "SHELVED_OFFLOADED":
        raise HTTPException(status_code=409, detail=f"server must be SHELVED_OFFLOADED to unshelve (current: {server.status})")

    server.status = "UNSHELVING"
    db.commit()
    cast_to_worker("unshelve", {
        **server_worker_payload(server),
        "_image_api_url": IMAGE_API_URL,
        "_block_store_url": BLOCK_STORE_API_URL,
        "_networking_url": NETWORKING_API_URL,
    })
    return {"server": server.name, "status": "unshelving"}


@app.get("/servers/{ref}/logs")
def logs(ref: str, _: CurrentUser, db: DB, lines: int = 50):
    server = require_server(db, ref)
    try:
        result = call_worker("get_logs", {**server_worker_payload(server), "lines": lines}, timeout=RPC_CALL_TIMEOUT)
        return result
    except Exception:
        return {
            "server": server.name,
            "domain": server.libvirt_domain_name,
            "output": "",
            "message": "console log streaming not available; use virsh console or check host libvirt logs",
        }


@app.get("/servers/{ref}/ssh-info")
def ssh_info(ref: str, _: CurrentUser, db: DB):
    server = require_server(db, ref)
    command = None
    if server.ip_address and server.keypair_name:
        command = f"ssh -i <private-key-for-{server.keypair_name}> {DEFAULT_VM_USER}@{server.ip_address}"
    return {
        "server": server.name,
        "backend": ONLY_BACKEND,
        "keypair_name": server.keypair_name,
        "ip_address": server.ip_address,
        "default_username": DEFAULT_VM_USER,
        "ssh_command": command,
    }


@app.post("/keypairs", status_code=201)
def create_keypair(payload: KeypairIn, _: CurrentUser, db: DB):
    if get_keypair(db, payload.name):
        raise HTTPException(status_code=409, detail="keypair already exists")
    private_key, public_key = generate_keypair_material()
    keypair = Keypair(name=payload.name, public_key=public_key, fingerprint=public_key_fingerprint(public_key))
    db.add(keypair)
    db.commit()
    db.refresh(keypair)
    data = obj_to_dict(keypair)
    data["private_key"] = private_key
    return data


@app.post("/keypairs/import", status_code=201)
def import_keypair(payload: KeypairImportIn, _: CurrentUser, db: DB):
    if get_keypair(db, payload.name):
        raise HTTPException(status_code=409, detail="keypair already exists")
    public_key = payload.public_key.strip()
    keypair = Keypair(name=payload.name, public_key=public_key, fingerprint=public_key_fingerprint(public_key))
    db.add(keypair)
    db.commit()
    db.refresh(keypair)
    return obj_to_dict(keypair)


@app.get("/keypairs")
def list_keypairs(_: CurrentUser, db: DB):
    return {"items": [obj_to_dict(k) for k in db.scalars(select(Keypair).order_by(Keypair.created_at)).all()]}


@app.get("/keypairs/{name}")
def show_keypair(name: str, _: CurrentUser, db: DB):
    return obj_to_dict(require_keypair(db, name))


@app.delete("/keypairs/{name}")
def delete_keypair(name: str, _: CurrentUser, db: DB):
    keypair = require_keypair(db, name)
    db.delete(keypair)
    db.commit()
    return {"status": "deleted", "id": keypair.id, "name": keypair.name}


# ── Server Lock / Unlock ───────────────────────────────────────────────────────

@app.post("/servers/{ref}/lock")
def lock_server(ref: str, current: CurrentUser, db: DB):
    """Lock a server so only admins can perform destructive operations."""
    server = require_server(db, ref)
    server.locked = True
    server.locked_by = current.get("username") or current.get("sub")
    db.commit()
    db.refresh(server)
    return {"server": server.name, "locked": True, "locked_by": server.locked_by}


@app.post("/servers/{ref}/unlock")
def unlock_server(ref: str, current: CurrentUser, db: DB):
    """Unlock a server."""
    server = require_server(db, ref)
    # Only admin or the user who locked it can unlock
    if server.locked and current.get("role") != "admin" and server.locked_by != (current.get("username") or current.get("sub")):
        raise HTTPException(status_code=403, detail="only the locking user or an admin can unlock this server")
    server.locked = False
    server.locked_by = None
    db.commit()
    db.refresh(server)
    return {"server": server.name, "locked": False}


# ── Server Tags ────────────────────────────────────────────────────────────────

@app.get("/servers/{ref}/tags")
def list_server_tags(ref: str, _: CurrentUser, db: DB):
    """List all tags for a server."""
    server = require_server(db, ref)
    tags = db.scalars(select(ServerTag).where(ServerTag.server_id == server.id)).all()
    return {"tags": [t.tag for t in tags]}


@app.put("/servers/{ref}/tags")
def replace_server_tags(ref: str, payload: dict, _: CurrentUser, db: DB):
    """Replace all tags on a server."""
    server = require_server(db, ref)
    new_tags = payload.get("tags", [])
    if not isinstance(new_tags, list):
        raise HTTPException(status_code=400, detail="'tags' must be a list of strings")
    # Delete existing and add new
    db.execute(text("DELETE FROM compute_server_tags WHERE server_id = :sid"), {"sid": server.id})
    for tag in new_tags:
        db.add(ServerTag(server_id=server.id, tag=str(tag)))
    db.commit()
    return {"tags": new_tags}


@app.put("/servers/{ref}/tags/{tag}")
def add_server_tag(ref: str, tag: str, _: CurrentUser, db: DB):
    """Add a single tag to a server."""
    server = require_server(db, ref)
    existing = db.get(ServerTag, (server.id, tag))
    if not existing:
        db.add(ServerTag(server_id=server.id, tag=tag))
        db.commit()
    return {"server": server.name, "tag": tag}


@app.delete("/servers/{ref}/tags/{tag}", status_code=204)
def delete_server_tag(ref: str, tag: str, _: CurrentUser, db: DB):
    """Remove a single tag from a server."""
    server = require_server(db, ref)
    row = db.get(ServerTag, (server.id, tag))
    if row:
        db.delete(row)
        db.commit()

