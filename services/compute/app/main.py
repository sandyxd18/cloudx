import base64
import hashlib
import os
import uuid
from datetime import datetime
from typing import Annotated

import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, DateTime, Integer, String, create_engine, inspect, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://mini:mini@postgres:5432/mini_openstack")
IMAGE_API_URL = os.getenv("IMAGE_API_URL", "http://image-api:8000").rstrip("/")
NETWORKING_API_URL = os.getenv("NETWORKING_API_URL", "http://networking-api:8000").rstrip("/")
BLOCK_STORE_API_URL = os.getenv("BLOCK_STORE_API_URL", "http://block-store-api:8000").rstrip("/")
COMPUTE_BACKEND = os.getenv("COMPUTE_BACKEND", "libvirt").lower()
COMPUTE_WORKER_URL = os.getenv("COMPUTE_WORKER_URL", "http://compute-worker:8010").rstrip("/")
DEFAULT_VM_USER = os.getenv("DEFAULT_VM_USER", "ubuntu")
ONLY_BACKEND = "libvirt"

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
    image_id: Mapped[str] = mapped_column(String)
    flavor: Mapped[str] = mapped_column(String)
    flavor_name: Mapped[str | None] = mapped_column(String, nullable=True)
    vcpus: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ram_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    disk_gb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    network_id: Mapped[str] = mapped_column(String)
    port_id: Mapped[str] = mapped_column(String)
    hostname: Mapped[str | None] = mapped_column(String, nullable=True)
    user_data: Mapped[str | None] = mapped_column(String, nullable=True)
    keypair_name: Mapped[str | None] = mapped_column(String, nullable=True)
    public_key_snapshot: Mapped[str | None] = mapped_column(String, nullable=True)
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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


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


class Keypair(Base):
    __tablename__ = "compute_keypairs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String, unique=True, index=True)
    public_key: Mapped[str] = mapped_column(String)
    fingerprint: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class ServerIn(BaseModel):
    name: str
    image: str
    flavor: str
    network: str
    key_name: str | None = None
    backend: str | None = None
    user_data: str | None = None


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


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


DB = Annotated[Session, Depends(get_db)]
app = FastAPI(title="mini OpenStack lab compute-api", version="0.2.0")


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
            )
            for column in string_columns:
                if column not in columns:
                    default = " DEFAULT 'libvirt'" if column == "backend" else ""
                    conn.execute(text(f"ALTER TABLE compute_servers ADD COLUMN {column} VARCHAR{default}"))
            for column in ("vcpus", "ram_mb", "disk_gb"):
                if column not in columns:
                    conn.execute(text(f"ALTER TABLE compute_servers ADD COLUMN {column} INTEGER"))
            conn.execute(text("UPDATE compute_servers SET backend = 'libvirt' WHERE backend IS NULL OR backend = 'simulator'"))
            conn.execute(text("UPDATE compute_servers SET flavor_name = flavor WHERE flavor_name IS NULL"))

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
        response = requests.get(url, timeout=8)
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=response_detail(response))
    return response.json()


def api_post(url: str, payload: dict):
    try:
        response = requests.post(url, json=payload, timeout=8)
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=response_detail(response))
    return response.json()


def api_delete(url: str, payload: dict | None = None):
    try:
        requests.delete(url, json=payload, timeout=20)
    except requests.RequestException:
        pass


def response_detail(response: requests.Response):
    try:
        return response.json().get("detail", response.text)
    except ValueError:
        return response.text


def worker_post(path: str, payload: dict):
    try:
        response = requests.post(f"{COMPUTE_WORKER_URL}{path}", json=payload, timeout=300)
    except requests.RequestException as exc:
        raise RuntimeError(str(exc)) from exc
    if response.status_code >= 400:
        raise RuntimeError(str(response_detail(response)))
    return response.json()


def worker_delete(path: str, payload: dict):
    try:
        response = requests.delete(f"{COMPUTE_WORKER_URL}{path}", json=payload, timeout=120)
    except requests.RequestException as exc:
        raise RuntimeError(str(exc)) from exc
    if response.status_code >= 400:
        raise RuntimeError(str(response_detail(response)))
    return response.json()


def release_server_volumes(server_id: str) -> dict | None:
    try:
        response = requests.post(f"{BLOCK_STORE_API_URL}/servers/{server_id}/volumes/detach-all", timeout=20)
    except requests.RequestException:
        return None
    if response.status_code >= 400:
        return None
    return response.json()


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


def server_worker_payload(server: Server, port: dict | None = None) -> dict:
    payload = obj_to_dict(server)
    payload["flavor_snapshot"] = {
        "name": server.flavor_name or server.flavor,
        "vcpus": server.vcpus,
        "ram_mb": server.ram_mb,
        "disk_gb": server.disk_gb,
    }
    if port:
        payload["port"] = port
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
    return {
        "uuid": server.id,
        "hostname": server.hostname or server.name,
        "name": server.name,
        "public_keys": public_keys,
        "meta": {},
        "devices": [],
        "instance_id": server.id,
        "local_hostname": server.hostname or server.name,
        "port_id": server.port_id,
        "fixed_ip": server.ip_address,
        "user_data": server.user_data or default_user_data(server),
    }


@app.get("/health")
def health():
    return {"status": "ok", "service": "compute-api", "default_backend": ONLY_BACKEND}


@app.get("/host/check")
def host_check():
    return api_get(f"{COMPUTE_WORKER_URL}/health")


@app.post("/flavors", status_code=201)
def create_flavor(payload: FlavorIn, db: DB):
    if get_flavor(db, payload.name):
        raise HTTPException(status_code=409, detail="flavor already exists")
    flavor = Flavor(**payload.model_dump())
    db.add(flavor)
    db.commit()
    db.refresh(flavor)
    return obj_to_dict(flavor)


@app.get("/flavors")
def list_flavors(db: DB):
    return {"items": [obj_to_dict(f) for f in db.scalars(select(Flavor).order_by(Flavor.created_at)).all()]}


@app.get("/flavors/{ref}")
def show_flavor(ref: str, db: DB):
    return obj_to_dict(require_flavor(db, ref))


@app.delete("/flavors/{ref}")
def delete_flavor(ref: str, db: DB):
    flavor = require_flavor(db, ref)
    db.delete(flavor)
    db.commit()
    return {"status": "deleted", "id": flavor.id, "name": flavor.name}


@app.post("/servers", status_code=201)
def create_server(payload: ServerIn, db: DB):
    if get_active_server_by_name(db, payload.name):
        raise HTTPException(status_code=409, detail="server already exists")
    free_deleted_server_name(db, payload.name)
    backend = selected_backend(payload.backend)
    keypair = require_keypair(db, payload.key_name) if payload.key_name else None
    flavor = require_flavor(db, payload.flavor)
    image = api_get(f"{IMAGE_API_URL}/images/{payload.image}")
    network = api_get(f"{NETWORKING_API_URL}/networks/{payload.network}")
    server_id = new_id()
    port = api_post(
        f"{NETWORKING_API_URL}/ports",
        {
            "name": server_port_name(payload.name, server_id),
            "network": network["id"],
            "device_id": server_id,
            "device_owner": "compute:nova-like",
        },
    )
    snapshot = flavor_snapshot(flavor)
    server = Server(
        id=server_id,
        name=payload.name,
        image_id=image["id"],
        flavor=flavor.name,
        flavor_name=snapshot["flavor_name"],
        vcpus=snapshot["vcpus"],
        ram_mb=snapshot["ram_mb"],
        disk_gb=snapshot["disk_gb"],
        network_id=network["id"],
        port_id=port["id"],
        hostname=payload.name,
        user_data=payload.user_data,
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
    worker_payload = server_worker_payload(server, port)
    try:
        result = worker_post(f"/servers/{server.id}/spawn", worker_payload)
        update_server_from_worker(server, result)
        server.status = "ACTIVE"
        server.error_message = None
    except Exception as exc:
        server.status = "ERROR"
        server.error_message = str(exc)
    db.commit()
    db.refresh(server)
    return obj_to_dict(server)


@app.get("/servers")
def list_servers(db: DB):
    return {"items": [server_to_dict(s) for s in db.scalars(select(Server).where(Server.status != "DELETED").order_by(Server.created_at)).all()]}


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
    server = db.scalar(query)
    if not server:
        raise HTTPException(status_code=404, detail="server metadata not found")
    return server_metadata_payload(server)


@app.get("/servers/{ref}")
def show_server(ref: str, db: DB):
    return server_to_dict(require_server(db, ref))


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
    try:
        worker_post(f"/servers/{server.id}/start", server_worker_payload(server))
        server.status = "ACTIVE"
        server.error_message = None
    except Exception as exc:
        server.status = "ERROR"
        server.error_message = str(exc)
    db.commit()
    db.refresh(server)
    return obj_to_dict(server)


@app.post("/servers/{ref}/stop")
def stop_server(ref: str, db: DB):
    server = require_server(db, ref)
    try:
        worker_post(f"/servers/{server.id}/stop", server_worker_payload(server))
        server.status = "SHUTOFF"
        server.error_message = None
    except Exception as exc:
        server.status = "ERROR"
        server.error_message = str(exc)
    db.commit()
    db.refresh(server)
    return obj_to_dict(server)


@app.post("/servers/{ref}/reboot")
def reboot_server(ref: str, db: DB):
    server = require_server(db, ref)
    server.status = "REBOOT"
    db.commit()
    try:
        worker_post(f"/servers/{server.id}/reboot", server_worker_payload(server))
        server.status = "ACTIVE"
        server.error_message = None
    except Exception as exc:
        server.status = "ERROR"
        server.error_message = str(exc)
    db.commit()
    db.refresh(server)
    return obj_to_dict(server)


@app.delete("/servers/{ref}")
def delete_server(ref: str, db: DB):
    server = require_server(db, ref)
    original_name = server.name
    try:
        worker_delete(f"/servers/{server.id}", server_worker_payload(server))
        server.error_message = None
    except Exception as exc:
        server.error_message = f"worker cleanup failed: {exc}"
    release_server_volumes(server.id)
    api_delete(f"{NETWORKING_API_URL}/ports/{server.port_id}")
    server.status = "DELETED"
    server.name = f"{original_name}-deleted-{server.id[:8]}"
    db.commit()
    return {"status": "deleted", "id": server.id, "name": original_name}


@app.post("/servers/{ref}/volumes/attach")
def attach_volume(ref: str, payload: VolumeAttachIn, db: DB):
    server = require_server(db, ref)
    if server.status != "ACTIVE":
        raise HTTPException(status_code=409, detail=f"server must be ACTIVE to attach volume; current status is {server.status}")
    try:
        result = worker_post(f"/servers/{server.id}/volumes/attach", {**server_worker_payload(server), "volume": payload.model_dump()})
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=f"volume attach failed on compute-worker: {exc}") from exc
    return {"status": "attached", "server_id": server.id, "volume_id": payload.volume_id, **result}


@app.post("/servers/{ref}/volumes/detach")
def detach_volume(ref: str, payload: VolumeAttachIn, db: DB):
    server = require_server(db, ref)
    if server.status not in {"ACTIVE", "SHUTOFF"}:
        raise HTTPException(status_code=409, detail=f"server must be ACTIVE or SHUTOFF to detach volume; current status is {server.status}")
    try:
        result = worker_post(f"/servers/{server.id}/volumes/detach", {**server_worker_payload(server), "volume": payload.model_dump()})
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=f"volume detach failed on compute-worker: {exc}") from exc
    return {"status": "detached", "server_id": server.id, "volume_id": payload.volume_id, **result}


@app.get("/servers/{ref}/console")
def console(ref: str, db: DB):
    server = require_server(db, ref)
    return {
        "server": server.name,
        "domain": server.libvirt_domain_name,
        "instruction": f"virsh -c qemu:///system console {server.libvirt_domain_name}",
        "note": "VNC is auto-assigned by libvirt; use virsh dumpxml to inspect the assigned port if needed.",
    }


@app.get("/servers/{ref}/console-url")
def console_url(ref: str, db: DB):
    server = require_server(db, ref)
    return {
        "server": server.name,
        "domain": server.libvirt_domain_name,
        "virsh_console": f"virsh -c qemu:///system console {server.libvirt_domain_name}",
        "vnc": {
            "status": "auto-assigned-by-libvirt",
            "inspect": f"virsh -c qemu:///system dumpxml {server.libvirt_domain_name}",
        },
        "novnc_url": None,
        "note": "noVNC proxy is not bundled yet; inspect the VNC port with virsh dumpxml or use virsh console.",
    }


@app.get("/servers/{ref}/logs")
def logs(ref: str, db: DB):
    server = require_server(db, ref)
    return {
        "server": server.name,
        "domain": server.libvirt_domain_name,
        "instruction": f"virsh -c qemu:///system console {server.libvirt_domain_name}",
        "message": "console log streaming is not implemented yet; use virsh console or host libvirt logs",
    }


@app.get("/servers/{ref}/ssh-info")
def ssh_info(ref: str, db: DB):
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
def create_keypair(payload: KeypairIn, db: DB):
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
def import_keypair(payload: KeypairImportIn, db: DB):
    if get_keypair(db, payload.name):
        raise HTTPException(status_code=409, detail="keypair already exists")
    public_key = payload.public_key.strip()
    keypair = Keypair(name=payload.name, public_key=public_key, fingerprint=public_key_fingerprint(public_key))
    db.add(keypair)
    db.commit()
    db.refresh(keypair)
    return obj_to_dict(keypair)


@app.get("/keypairs")
def list_keypairs(db: DB):
    return {"items": [obj_to_dict(k) for k in db.scalars(select(Keypair).order_by(Keypair.created_at)).all()]}


@app.get("/keypairs/{name}")
def show_keypair(name: str, db: DB):
    return obj_to_dict(require_keypair(db, name))


@app.delete("/keypairs/{name}")
def delete_keypair(name: str, db: DB):
    keypair = require_keypair(db, name)
    db.delete(keypair)
    db.commit()
    return {"status": "deleted", "id": keypair.id, "name": keypair.name}
