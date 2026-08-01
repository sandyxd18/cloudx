"""
cloudx block-store-api (refactored — stateless, no privileged)
================================================================

Handles CRUD, status management, and volume type management.
All storage backend operations (LVM/Ceph) are delegated to block-store-worker
via RabbitMQ (AMQP) RPC.

Queue used:
  Cast → 'block-store'   for async ops (create, delete, extend, snapshot)
  Call → 'block-store'   for sync ops (attach/detach via compute-worker)

Status transitions:
  create_volume  → status='creating'  → worker → 'available' (or 'error')
  delete_volume  → status='deleting'  → worker → (record deleted)
  extend_volume  → status='extending' → worker → 'available'
  snapshot       → status='creating'  → worker → 'available'
"""

import logging
import os
import sys
import uuid
from datetime import datetime
from typing import Annotated

import requests
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, DateTime, Integer, String, create_engine, inspect, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

try:
    from app.auth_middleware import CurrentUser, AdminUser, OptionalAuth
except ImportError:
    from auth_middleware import CurrentUser, AdminUser, OptionalAuth

# Import shared RPC library
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
try:
    from shared.rpc import RPCClient
except ImportError:
    from rpc import RPCClient

# Load shared request_id_middleware
try:
    from shared.request_id_middleware import RequestIDMiddleware
except ImportError:
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
        from shared.request_id_middleware import RequestIDMiddleware
    except ImportError:
        RequestIDMiddleware = None

log = logging.getLogger("block-store-api")

# ── Configuration ──────────────────────────────────────────────────────────────

DATABASE_URL    = os.getenv("DATABASE_URL", "postgresql+psycopg://mini:mini@postgres:5432/mini_openstack")
COMPUTE_API_URL = os.getenv("COMPUTE_API_URL", "http://compute-api:8000").rstrip("/")
IMAGE_API_URL   = os.getenv("IMAGE_API_URL", "http://image-api:8000").rstrip("/")
RABBITMQ_URL    = os.getenv("RABBITMQ_URL", "amqp://cloudx:cloudx@rabbitmq:5672/")
RPC_CALL_TIMEOUT = int(os.getenv("RPC_CALL_TIMEOUT", "30"))

# These are kept only for the health endpoint — the API itself doesn't use drivers
STORAGE_LVM_DRIVER      = os.getenv("STORAGE_LVM_DRIVER", "false").lower() == "true"
STORAGE_CEPH_RBD_DRIVER = os.getenv("STORAGE_CEPH_RBD_DRIVER", "false").lower() == "true"
LVM_VG_NAME  = os.getenv("LVM_VG_NAME", "")
CEPH_POOL    = os.getenv("CEPH_POOL", "")

# ── RPC Client (initialized on startup) ───────────────────────────────────────
_rpc: RPCClient | None = None

# ── Database ───────────────────────────────────────────────────────────────────

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def new_id() -> str:
    return str(uuid.uuid4())


# ── ORM models ─────────────────────────────────────────────────────────────────

DISK_TYPES = ("sata_ssd", "sas_ssd", "nvme_ssd", "sata_hdd", "sas_hdd")


class VolumeType(Base):
    """Performance profile for volumes — analogous to AWS EBS volume types."""
    __tablename__ = "block_store_volume_types"
    id:             Mapped[str]      = mapped_column(String, primary_key=True, default=new_id)
    name:           Mapped[str]      = mapped_column(String, unique=True, index=True)
    backend:        Mapped[str]      = mapped_column(String, default="lvm")
    disk_type:      Mapped[str]      = mapped_column(String, default="sata_ssd")
    description:    Mapped[str|None] = mapped_column(String, nullable=True)
    max_iops:       Mapped[int|None] = mapped_column(Integer, nullable=True)
    max_throughput: Mapped[int|None] = mapped_column(Integer, nullable=True)
    is_public:      Mapped[bool]     = mapped_column(Boolean, default=True)
    created_at:     Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Volume(Base):
    __tablename__ = "block_store_volumes"

    id:                    Mapped[str]      = mapped_column(String, primary_key=True, default=new_id)
    name:                  Mapped[str]      = mapped_column(String, unique=True, index=True)
    size_gb:               Mapped[int]      = mapped_column(Integer)
    status:                Mapped[str]      = mapped_column(String, default="creating")
    attached_to_server_id: Mapped[str|None] = mapped_column(String, nullable=True)
    path:                  Mapped[str]      = mapped_column(String, default="")
    backend:               Mapped[str|None] = mapped_column(String, nullable=True, default="lvm")
    bootable:              Mapped[bool]     = mapped_column(Boolean, default=False)
    image_id:              Mapped[str|None] = mapped_column(String, nullable=True)
    snapshot_id:           Mapped[str|None] = mapped_column(String, nullable=True)
    source_volume_id:      Mapped[str|None] = mapped_column(String, nullable=True)
    source_type:           Mapped[str|None] = mapped_column(String, nullable=True)
    target_device:         Mapped[str|None] = mapped_column(String, nullable=True)
    project_id:            Mapped[str|None] = mapped_column(String, nullable=True, index=True)
    volume_type:           Mapped[str|None] = mapped_column(String, nullable=True)
    error_message:         Mapped[str|None] = mapped_column(String, nullable=True)
    created_at:            Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Snapshot(Base):
    __tablename__ = "block_store_snapshots"

    id:         Mapped[str]      = mapped_column(String, primary_key=True, default=new_id)
    name:       Mapped[str]      = mapped_column(String, unique=True, index=True)
    volume_id:  Mapped[str]      = mapped_column(String)
    size_gb:    Mapped[int]      = mapped_column(Integer)
    status:     Mapped[str]      = mapped_column(String, default="creating")
    path:       Mapped[str]      = mapped_column(String, default="")
    backend:    Mapped[str|None] = mapped_column(String, nullable=True, default="lvm")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class VolumeQuota(Base):
    """Per-project volume quota limits."""
    __tablename__ = "block_store_volume_quotas"
    project_id:  Mapped[str]     = mapped_column(String, primary_key=True)
    volumes:     Mapped[int]     = mapped_column(Integer, default=10)     # max number of volumes
    gigabytes:   Mapped[int]     = mapped_column(Integer, default=1000)   # max total GB
    snapshots:   Mapped[int]     = mapped_column(Integer, default=10)     # max snapshots
    created_at:  Mapped[datetime]= mapped_column(DateTime, default=datetime.utcnow)


# ── Pydantic schemas ───────────────────────────────────────────────────────────

class VolumeIn(BaseModel):
    name:          str
    size_gb:       int | None = Field(None, gt=0)
    size:          int | None = None
    image:         str | None = None
    image_id:      str | None = None
    snapshot:      str | None = None
    source_volume: str | None = None
    bootable:      bool = False
    volume_type:   str | None = None


class VolumeInternalIn(BaseModel):
    name:             str
    size_gb:          int = Field(gt=0)
    image_id:         str | None = None
    image_ref:        str | None = None
    snapshot_id:      str | None = None
    source_volume_id: str | None = None
    bootable:         bool = False
    source_type:      str | None = None
    volume_type:      str | None = None


class AttachIn(BaseModel):
    server: str


class SnapshotIn(BaseModel):
    name: str


class InternalAttachIn(BaseModel):
    server_id:     str
    target_device: str | None = None


class VolumeTypeIn(BaseModel):
    name:           str
    description:    str | None = None
    backend:        str = "lvm"
    disk_type:      str = "sata_ssd"
    max_iops:       int | None = None
    max_throughput: int | None = None
    is_public:      bool = True


# ── FastAPI app ────────────────────────────────────────────────────────────────

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


DB = Annotated[Session, Depends(get_db)]
app = FastAPI(title="cloudx block-store-api", version="3.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
if RequestIDMiddleware:
    app.add_middleware(RequestIDMiddleware)


# ── Startup ────────────────────────────────────────────────────────────────────

@app.on_event("startup")
def startup():
    global _rpc
    if not STORAGE_LVM_DRIVER and not STORAGE_CEPH_RBD_DRIVER:
        raise RuntimeError(
            "No storage driver is enabled! "
            "Set STORAGE_LVM_DRIVER=true and/or STORAGE_CEPH_RBD_DRIVER=true in .env"
        )
    Base.metadata.create_all(bind=engine)
    ensure_columns()
    _bootstrap_quotas()
    _rpc = RPCClient(RABBITMQ_URL)
    log.info("Block-store-api ready (RabbitMQ RPC mode)")
    log.info("Drivers: lvm=%s ceph=%s", STORAGE_LVM_DRIVER, STORAGE_CEPH_RBD_DRIVER)


def _bootstrap_quotas() -> None:
    """Inject default quotas for admin project if not already present."""
    db = SessionLocal()
    try:
        # Try to find the admin project - use project_id "admin" as fallback
        ADMIN_PROJECT_ID = os.getenv("AUTH_ADMIN_PROJECT", "admin")
        existing = db.get(VolumeQuota, ADMIN_PROJECT_ID)
        if not existing:
            db.add(VolumeQuota(
                project_id=ADMIN_PROJECT_ID,
                volumes=50,
                gigabytes=10000,
                snapshots=50,
            ))
            db.commit()
            log.info("[block-store] bootstrap: default quota injected for project '%s'", ADMIN_PROJECT_ID)
    except Exception as exc:
        log.warning("[block-store] bootstrap_quotas failed: %s", exc)
    finally:
        db.close()


@app.on_event("shutdown")
def shutdown():
    if _rpc:
        _rpc.close()


def ensure_columns() -> None:
    """Add any missing columns to existing tables (safe migration)."""
    inspector = inspect(engine)
    tables = inspector.get_table_names()

    if "block_store_volumes" in tables:
        columns = {col["name"] for col in inspector.get_columns("block_store_volumes")}
        with engine.begin() as conn:
            for col, ddl in (
                ("bootable",         "BOOLEAN DEFAULT FALSE"),
                ("image_id",         "VARCHAR"),
                ("snapshot_id",      "VARCHAR"),
                ("source_volume_id", "VARCHAR"),
                ("source_type",      "VARCHAR"),
                ("target_device",    "VARCHAR"),
                ("project_id",       "VARCHAR"),
                ("volume_type",      "VARCHAR"),
                ("backend",          "VARCHAR DEFAULT 'lvm'"),
                ("error_message",    "VARCHAR"),
            ):
                if col not in columns:
                    conn.execute(text(f"ALTER TABLE block_store_volumes ADD COLUMN {col} {ddl}"))
            conn.execute(text("UPDATE block_store_volumes SET bootable = FALSE WHERE bootable IS NULL"))

    if "block_store_snapshots" in tables:
        columns = {col["name"] for col in inspector.get_columns("block_store_snapshots")}
        with engine.begin() as conn:
            if "backend" not in columns:
                conn.execute(text("ALTER TABLE block_store_snapshots ADD COLUMN backend VARCHAR DEFAULT 'lvm'"))

    if "block_store_volume_types" in tables:
        columns = {col["name"] for col in inspector.get_columns("block_store_volume_types")}
        with engine.begin() as conn:
            for col, ddl in (
                ("disk_type",      "VARCHAR DEFAULT 'sata_ssd'"),
                ("max_iops",       "INTEGER"),
                ("max_throughput", "INTEGER"),
            ):
                if col not in columns:
                    conn.execute(text(f"ALTER TABLE block_store_volume_types ADD COLUMN {col} {ddl}"))
            conn.execute(text(
                "DELETE FROM block_store_volume_types "
                "WHERE name IN ('lvm', 'ceph') AND disk_type IS NULL"
            ))

    # Ensure quota table exists (create_all handles this via ORM model)
    # The VolumeQuota model is registered in Base, so create_all above covers it.


# ── DB helpers ─────────────────────────────────────────────────────────────────

def obj_to_dict(obj):
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}


def _fmt_ts(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000000")


def volume_to_os_dict(volume: Volume) -> dict:
    """Serialize a Volume to an OpenStack-compatible dict."""
    attachments = []
    if volume.attached_to_server_id:
        attachments = [{
            "id": volume.id,
            "volume_id": volume.id,
            "server_id": volume.attached_to_server_id,
            "host_name": "single-node",
            "device": f"/dev/{volume.target_device or 'vda'}",
            "attached_at": _fmt_ts(volume.created_at),
            "attachment_id": volume.id,
        }]

    return {
        "attachments": attachments,
        "availability_zone": "nova",
        "backup_id": None,
        "bootable": str(volume.bootable).lower() == "true" or volume.bootable is True,
        "cluster_name": None,
        "consumes_quota": True,
        "created_at": _fmt_ts(volume.created_at),
        "description": "",
        "encrypted": False,
        "group_id": None,
        "id": volume.id,
        "multiattach": False,
        "name": volume.name or "",
        "os-vol-host-attr:host": f"single-node@{volume.backend or 'lvm'}#{volume.backend or 'lvm'}",
        "os-vol-mig-status-attr:migstat": None,
        "os-vol-mig-status-attr:name_id": None,
        "os-vol-tenant-attr:tenant_id": volume.project_id,
        "properties": {},
        "provider_id": None,
        "replication_status": None,
        "size": volume.size_gb,
        "snapshot_id": volume.snapshot_id,
        "source_volid": volume.source_volume_id,
        "status": volume.status,
        "type": volume.volume_type or volume.backend or "lvm",
        "updated_at": _fmt_ts(volume.created_at),
        "user_id": None,
        "volume_image_metadata": {},
        # Internal fields kept for backward compat
        "size_gb": volume.size_gb,
        "path": volume.path,
        "backend": volume.backend,
        "image_id": volume.image_id,
        "target_device": volume.target_device,
        "attached_to_server_id": volume.attached_to_server_id,
        "project_id": volume.project_id,
        "error_message": volume.error_message,
    }


def snapshot_to_os_dict(snapshot: Snapshot) -> dict:
    """Serialize a Snapshot to an OpenStack-compatible dict."""
    return {
        "created_at": _fmt_ts(snapshot.created_at),
        "description": "",
        "id": snapshot.id,
        "name": snapshot.name or "",
        "size": snapshot.size_gb,
        "status": snapshot.status,
        "updated_at": _fmt_ts(snapshot.created_at),
        "volume_id": snapshot.volume_id,
        # Internal
        "size_gb": snapshot.size_gb,
        "path": snapshot.path,
        "backend": snapshot.backend,
    }


def get_volume(db: Session, ref: str):
    return db.get(Volume, ref) or db.scalar(select(Volume).where(Volume.name == ref))


def require_volume(db: Session, ref: str) -> Volume:
    volume = get_volume(db, ref)
    if not volume:
        raise HTTPException(status_code=404, detail=f"volume '{ref}' not found")
    return volume


def get_snapshot(db: Session, ref: str):
    return db.get(Snapshot, ref) or db.scalar(select(Snapshot).where(Snapshot.name == ref))


def require_snapshot(db: Session, ref: str) -> Snapshot:
    snapshot = get_snapshot(db, ref)
    if not snapshot:
        raise HTTPException(status_code=404, detail=f"snapshot '{ref}' not found")
    return snapshot


def _resolve_volume_type(db: Session, type_name: str | None) -> VolumeType:
    if type_name:
        vt = db.scalar(select(VolumeType).where(VolumeType.name == type_name))
        if not vt:
            available = db.scalars(select(VolumeType.name)).all()
            hint = f"Available types: {', '.join(available)}" if available else "No volume types have been created yet."
            raise HTTPException(
                status_code=400,
                detail=f"Volume type '{type_name}' not found. {hint}"
            )
        return vt

    vt = db.scalar(select(VolumeType).order_by(VolumeType.name))
    if not vt:
        raise HTTPException(
            status_code=400,
            detail=(
                "No volume types are configured. "
                "An admin must create at least one volume type before creating volumes. "
                "Example: cloudx volume-type create gp2 --backend lvm --disk-type sata_ssd --max-iops 3000 --max-throughput 125"
            ),
        )
    return vt


def _validate_backend(vt: VolumeType) -> None:
    if vt.backend == "lvm" and not STORAGE_LVM_DRIVER:
        raise HTTPException(status_code=400, detail="LVM driver is disabled (STORAGE_LVM_DRIVER=false)")
    if vt.backend == "ceph" and not STORAGE_CEPH_RBD_DRIVER:
        raise HTTPException(status_code=400, detail="Ceph driver is disabled (STORAGE_CEPH_RBD_DRIVER=false)")


# ── HTTP helpers ───────────────────────────────────────────────────────────────

def _response_detail(response: requests.Response) -> str:
    try:
        return response.json().get("detail", response.text)
    except ValueError:
        return response.text


def get_server(ref: str) -> dict:
    try:
        response = requests.get(f"{COMPUTE_API_URL}/servers/{ref}", timeout=8)
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=_response_detail(response))
    return response.json()


# ── RPC helpers ────────────────────────────────────────────────────────────────

def cast_to_worker(method: str, payload: dict) -> None:
    """Fire-and-forget to block-store-worker via RabbitMQ."""
    _rpc.cast("block-store", method, payload)
    log.info("CAST block-store.%s for %s", method, payload.get("volume_id") or payload.get("snapshot_id", "?"))


def next_target_device(db: Session, server_id: str) -> str:
    used = set(
        item for item in db.scalars(
            select(Volume.target_device).where(
                Volume.attached_to_server_id == server_id,
                Volume.target_device.is_not(None),
            )
        ).all()
        if item
    )
    for letter in "bcdefghijklmnopqrstuvwxyz":
        candidate = f"sd{letter}"
        if candidate not in used:
            return candidate
    raise HTTPException(status_code=409, detail="no available disk target devices for server")


# ── Health ─────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "block-store-api",
        "mode": "rpc",
        "drivers": {
            "lvm":  STORAGE_LVM_DRIVER,
            "ceph": STORAGE_CEPH_RBD_DRIVER,
        },
        "lvm_vg":    LVM_VG_NAME if STORAGE_LVM_DRIVER else None,
        "ceph_pool": CEPH_POOL if STORAGE_CEPH_RBD_DRIVER else None,
    }


# ── Volume CRUD ────────────────────────────────────────────────────────────────

@app.post("/volumes", status_code=201)
def create_volume(payload: VolumeIn, current: CurrentUser, db: DB):
    size = payload.size_gb or payload.size
    if not size:
        raise HTTPException(status_code=400, detail="size_gb is required")
    project_id = current["project_id"]
    # ── Quota validation ───────────────────────────────────────────
    quota = db.get(VolumeQuota, project_id)
    if quota:
        current_vols = db.scalars(
            select(Volume).where(
                Volume.project_id == project_id,
                Volume.status.not_in(["deleting", "error"]),
            )
        ).all()
        if len(current_vols) >= quota.volumes:
            raise HTTPException(
                status_code=413,
                detail=f"Volume quota exceeded: {len(current_vols)}/{quota.volumes} volumes used",
            )
        used_gb = sum(v.size_gb for v in current_vols)
        if used_gb + size > quota.gigabytes:
            raise HTTPException(
                status_code=413,
                detail=f"Volume GB quota exceeded: {used_gb + size}/{quota.gigabytes} GB would be used",
            )
    internal = VolumeInternalIn(
        name=payload.name,
        size_gb=size,
        image_ref=payload.image or payload.image_id,
        snapshot_id=payload.snapshot,
        source_volume_id=payload.source_volume,
        bootable=payload.bootable,
        volume_type=payload.volume_type,
    )
    return _create_volume_record(db, internal, project_id=project_id)


@app.post("/internal/volumes", status_code=201)
def create_volume_internal(payload: VolumeInternalIn, db: DB):
    return _create_volume_record(db, payload)


def _create_volume_record(db: Session, payload: VolumeInternalIn, project_id: str | None = None) -> dict:
    if get_volume(db, payload.name):
        raise HTTPException(status_code=409, detail="volume already exists")

    source_count = sum(bool(x) for x in (
        payload.image_ref or payload.image_id,
        payload.snapshot_id,
        payload.source_volume_id,
    ))
    if source_count > 1:
        raise HTTPException(status_code=400, detail="choose only one source: image, snapshot, or source_volume")

    vt = _resolve_volume_type(db, payload.volume_type)
    _validate_backend(vt)
    backend = vt.backend

    volume_id = new_id()
    image_ref = payload.image_ref or payload.image_id
    source_type = payload.source_type
    bootable    = payload.bootable
    snapshot_id = None
    source_volume_id = None

    # Validate sources exist before creating record
    if image_ref:
        source_type = "image"
        bootable = True
    elif payload.snapshot_id:
        snap = require_snapshot(db, payload.snapshot_id)
        snapshot_id = snap.id
        source_type = "snapshot"
        backend = snap.backend or backend
    elif payload.source_volume_id:
        src = require_volume(db, payload.source_volume_id)
        source_volume_id = src.id
        source_type = "volume"
        bootable = src.bootable
        backend = src.backend or backend
    else:
        source_type = source_type or "blank"

    # Create DB record immediately with status='creating'
    volume = Volume(
        id=volume_id,
        name=payload.name,
        size_gb=payload.size_gb,
        status="creating",
        path="",                          # worker fills this in
        backend=backend,
        bootable=bootable,
        image_id=image_ref or None,
        snapshot_id=snapshot_id,
        source_volume_id=source_volume_id,
        source_type=source_type,
        volume_type=vt.name,
        project_id=project_id,
    )
    db.add(volume)
    db.commit()
    db.refresh(volume)

    # Cast to worker for actual storage creation
    cast_to_worker("create_volume", {
        "volume_id":       volume_id,
        "name":            payload.name,
        "size_gb":         payload.size_gb,
        "backend":         backend,
        "image_ref":       image_ref,
        "snapshot_id":     snapshot_id,
        "source_volume_id": source_volume_id,
        "bootable":        bootable,
        "_image_api_url":  IMAGE_API_URL,
    })

    return obj_to_dict(volume)


@app.get("/internal/volumes/{ref}")
def get_volume_internal(ref: str, db: DB):
    """No-auth endpoint for internal services (e.g. compute-worker) to poll volume status."""
    volume = require_volume(db, ref)
    return {
        "id":       volume.id,
        "name":     volume.name,
        "size_gb":  volume.size_gb,
        "status":   volume.status,
        "path":     volume.path,
        "bootable": volume.bootable,
        "backend":  volume.backend,
        "target_device": volume.target_device,
        "error_message": volume.error_message,
    }


@app.delete("/internal/volumes/{ref}", status_code=202)
def delete_volume_internal(ref: str, db: DB):
    """No-auth endpoint for internal services (e.g. compute-worker) to delete root volumes."""
    volume = require_volume(db, ref)
    if volume.status == "in-use":
        raise HTTPException(status_code=409, detail="volume is in-use, detach first")
    volume.status = "deleting"
    db.commit()
    cast_to_worker("delete_volume", {
        "volume_id": volume.id,
        "name":      volume.name,
        "path":      volume.path,
        "backend":   volume.backend,
    })
    return {"status": "deleting", "id": volume.id}


@app.get("/volumes")
def list_volumes(current: CurrentUser, db: DB):
    q = select(Volume)
    if current.get("role") != "admin":
        q = q.where(Volume.project_id == current["project_id"])
    return {"items": [volume_to_os_dict(v) for v in db.scalars(q.order_by(Volume.created_at)).all()]}


@app.get("/volumes/{ref}")
def show_volume(ref: str, _: CurrentUser, db: DB):
    return volume_to_os_dict(require_volume(db, ref))


@app.delete("/volumes/{ref}")
def delete_volume(ref: str, _: CurrentUser, db: DB):
    volume = require_volume(db, ref)
    if volume.status == "in-use":
        raise HTTPException(status_code=409, detail="detach volume before delete")
    volume.status = "deleting"
    db.commit()
    # Cast to worker for backend cleanup, then conductor will delete the record
    cast_to_worker("delete_volume", {
        "volume_id": volume.id,
        "name":      volume.name,
        "path":      volume.path,
        "backend":   volume.backend,
    })
    return {"status": "deleting", "id": volume.id, "name": volume.name}


@app.post("/volumes/{ref}/extend")
def extend_volume(ref: str, payload: dict, _: CurrentUser, db: DB):
    volume = require_volume(db, ref)
    if volume.status == "in-use":
        raise HTTPException(status_code=409, detail="cannot extend an attached volume")
    new_size = payload.get("new_size") or payload.get("new_size_gb")
    if not new_size or new_size <= volume.size_gb:
        raise HTTPException(status_code=400, detail=f"new_size must be > current size ({volume.size_gb} GB)")
    volume.status = "extending"
    db.commit()
    cast_to_worker("extend_volume", {
        "volume_id": volume.id,
        "name":      volume.name,
        "path":      volume.path,
        "backend":   volume.backend,
        "new_size_gb": new_size,
    })
    db.refresh(volume)
    return obj_to_dict(volume)


# ── Internal volume attachment (called by compute-worker) ──────────────────────

@app.post("/internal/volumes/{ref}/attach-server")
def internal_attach_server(ref: str, payload: InternalAttachIn, db: DB):
    """Called by compute-worker after spawning a VM to mark root volume as in-use."""
    volume = require_volume(db, ref)
    if volume.status == "in-use" and volume.attached_to_server_id != payload.server_id:
        raise HTTPException(status_code=409, detail="volume is already attached")
    volume.attached_to_server_id = payload.server_id
    volume.target_device = payload.target_device or volume.target_device or (
        "sda" if volume.bootable else next_target_device(db, payload.server_id)
    )
    volume.status = "in-use"
    db.commit()
    db.refresh(volume)
    return obj_to_dict(volume)


@app.post("/internal/volumes/{ref}/detach-server")
def internal_detach_server(ref: str, db: DB):
    """Called by compute-worker to release a volume after VM destruction."""
    volume = require_volume(db, ref)
    volume.attached_to_server_id = None
    volume.target_device = None
    volume.status = "available"
    db.commit()
    db.refresh(volume)
    return obj_to_dict(volume)


@app.post("/servers/{server_id}/volumes/detach-all")
def detach_server_volumes(server_id: str, db: DB):
    """Release all volumes attached to a server (called during VM delete)."""
    volumes = db.scalars(select(Volume).where(Volume.attached_to_server_id == server_id)).all()
    detached = []
    for volume in volumes:
        volume.attached_to_server_id = None
        volume.target_device = None
        volume.status = "available"
        detached.append(obj_to_dict(volume))
    db.commit()
    return {"server_id": server_id, "detached": detached}


@app.post("/volumes/{ref}/force-detach")
def force_detach_volume(ref: str, db: DB):
    volume = require_volume(db, ref)
    volume.attached_to_server_id = None
    volume.target_device = None
    volume.status = "available"
    db.commit()
    db.refresh(volume)
    return obj_to_dict(volume)


@app.post("/volumes/{ref}/attach")
def attach_volume(ref: str, payload: AttachIn, db: DB):
    """Hotplug attach via compute-api → compute-worker (hotplug path)."""
    volume = require_volume(db, ref)
    if volume.status == "in-use":
        raise HTTPException(status_code=409, detail="volume is already attached")
    server = get_server(payload.server)
    if server.get("status") not in {"ACTIVE", "SHUTOFF"}:
        raise HTTPException(
            status_code=409,
            detail=f"VM must be ACTIVE or SHUTOFF before attach; current status is {server.get('status')}",
        )
    target_device = next_target_device(db, server["id"])
    # Call compute-api which casts to compute-worker
    try:
        resp = requests.post(
            f"{COMPUTE_API_URL}/servers/{server['id']}/volumes/attach",
            json={
                "volume_id":   volume.id,
                "volume_name": volume.name,
                "path":        volume.path,
                "backend":     volume.backend or "lvm",
                "target_dev":  target_device,
            },
            timeout=60,
        )
        if resp.status_code >= 400:
            raise HTTPException(status_code=502, detail=f"compute-api attach failed: {_response_detail(resp)}")
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    volume.attached_to_server_id = server["id"]
    volume.target_device = target_device
    volume.status = "in-use"
    db.commit()
    db.refresh(volume)
    return obj_to_dict(volume)


@app.post("/volumes/{ref}/detach")
def detach_volume(ref: str, db: DB):
    """Hotplug detach via compute-api → compute-worker (hotplug path)."""
    volume = require_volume(db, ref)
    if volume.status != "in-use" or not volume.attached_to_server_id:
        raise HTTPException(status_code=409, detail="volume is not attached")
    try:
        resp = requests.post(
            f"{COMPUTE_API_URL}/servers/{volume.attached_to_server_id}/volumes/detach",
            json={
                "volume_id":   volume.id,
                "volume_name": volume.name,
                "path":        volume.path,
                "backend":     volume.backend or "lvm",
                "target_dev":  volume.target_device,
            },
            timeout=60,
        )
        if resp.status_code >= 400:
            raise HTTPException(status_code=502, detail=f"compute-api detach failed: {_response_detail(resp)}")
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    volume.attached_to_server_id = None
    volume.target_device = None
    volume.status = "available"
    db.commit()
    db.refresh(volume)
    return obj_to_dict(volume)


# ── Snapshots ──────────────────────────────────────────────────────────────────

@app.post("/volumes/{ref}/snapshots", status_code=201)
def create_volume_snapshot(ref: str, payload: SnapshotIn, _: CurrentUser, db: DB):
    return _do_create_snapshot(db, ref, payload.name)


@app.post("/snapshots", status_code=201)
def create_snapshot(payload: SnapshotIn, volume: str, db: DB):
    return _do_create_snapshot(db, volume, payload.name)


def _do_create_snapshot(db: Session, volume_ref: str, snap_name: str) -> dict:
    if get_snapshot(db, snap_name):
        raise HTTPException(status_code=409, detail="snapshot already exists")
    source = require_volume(db, volume_ref)

    snapshot_id = new_id()
    import math
    cow_gb = max(1, math.ceil(source.size_gb * 0.2))

    # Create record with status='creating'
    snap = Snapshot(
        id=snapshot_id,
        name=snap_name,
        volume_id=source.id,
        size_gb=source.size_gb,
        status="creating",
        path="",
        backend=source.backend,
    )
    db.add(snap)
    db.commit()
    db.refresh(snap)

    cast_to_worker("create_snapshot", {
        "snapshot_id":  snapshot_id,
        "name":         snap_name,
        "volume_id":    source.id,
        "volume_path":  source.path,
        "backend":      source.backend,
        "cow_gb":       cow_gb,
    })

    return obj_to_dict(snap)


@app.get("/snapshots")
def list_snapshots(_: CurrentUser, db: DB):
    return {"items": [snapshot_to_os_dict(s) for s in db.scalars(select(Snapshot).order_by(Snapshot.created_at)).all()]}


@app.get("/snapshots/{ref}")
def show_snapshot(ref: str, _: CurrentUser, db: DB):
    return snapshot_to_os_dict(require_snapshot(db, ref))


@app.delete("/snapshots/{ref}")
def delete_snapshot(ref: str, _: CurrentUser, db: DB):
    snapshot = require_snapshot(db, ref)
    snapshot.status = "deleting"
    db.commit()
    cast_to_worker("delete_snapshot", {
        "snapshot_id": snapshot.id,
        "name":        snapshot.name,
        "path":        snapshot.path,
        "backend":     snapshot.backend,
    })
    return {"status": "deleting", "id": snapshot.id, "name": snapshot.name}


# ── Backup ─────────────────────────────────────────────────────────────────────

@app.post("/volumes/{ref}/backup")
def backup_volume(ref: str, payload: SnapshotIn, _: CurrentUser, db: DB):
    """Create a snapshot as backup (async)."""
    volume = require_volume(db, ref)
    backup_id = new_id()
    import math
    cow_gb = max(1, math.ceil(volume.size_gb * 0.2))

    snap = Snapshot(
        id=backup_id,
        name=f"bkp-{payload.name}",
        volume_id=volume.id,
        size_gb=volume.size_gb,
        status="creating",
        path="",
        backend=volume.backend,
    )
    db.add(snap)
    db.commit()
    db.refresh(snap)

    cast_to_worker("create_snapshot", {
        "snapshot_id": backup_id,
        "name":        f"bkp-{backup_id.replace('-', '')[:22]}",
        "volume_id":   volume.id,
        "volume_path": volume.path,
        "backend":     volume.backend,
        "cow_gb":      cow_gb,
    })

    return obj_to_dict(snap)


# ── Volume Types ───────────────────────────────────────────────────────────────

def _vt_to_dict(vt: VolumeType) -> dict:
    return {c.name: getattr(vt, c.name) for c in vt.__table__.columns}


@app.post("/volume-types", status_code=201)
def create_volume_type(payload: VolumeTypeIn, _: AdminUser, db: DB):
    if payload.disk_type not in DISK_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid disk_type '{payload.disk_type}'. Valid values: {', '.join(DISK_TYPES)}"
        )
    if payload.backend not in ("lvm", "ceph"):
        raise HTTPException(status_code=400, detail="backend must be 'lvm' or 'ceph'")
    if db.scalar(select(VolumeType).where(VolumeType.name == payload.name)):
        raise HTTPException(status_code=409, detail=f"volume type '{payload.name}' already exists")
    vt = VolumeType(**payload.model_dump())
    db.add(vt)
    db.commit()
    db.refresh(vt)
    return _vt_to_dict(vt)


@app.get("/volume-types")
def list_volume_types(db: DB, _: CurrentUser = None):
    return {"items": [_vt_to_dict(vt) for vt in db.scalars(select(VolumeType).order_by(VolumeType.name)).all()]}


@app.get("/volume-types/{ref}")
def show_volume_type(ref: str, _: CurrentUser, db: DB):
    vt = db.get(VolumeType, ref) or db.scalar(select(VolumeType).where(VolumeType.name == ref))
    if not vt:
        raise HTTPException(status_code=404, detail=f"volume type '{ref}' not found")
    return _vt_to_dict(vt)


@app.delete("/volume-types/{ref}")
def delete_volume_type(ref: str, _: AdminUser, db: DB):
    vt = db.get(VolumeType, ref) or db.scalar(select(VolumeType).where(VolumeType.name == ref))
    if not vt:
        raise HTTPException(status_code=404, detail=f"volume type '{ref}' not found")
    in_use = db.scalar(select(Volume).where(Volume.volume_type == vt.name))
    if in_use:
        raise HTTPException(status_code=409, detail=f"volume type '{vt.name}' is in use by existing volumes")
    db.delete(vt)
    db.commit()
    return {"status": "deleted", "name": vt.name}


# ── Volume Quotas ─────────────────────────────────────────────────────────────

class QuotaIn(BaseModel):
    volumes:   int | None = None
    gigabytes: int | None = None
    snapshots: int | None = None


def _quota_usage(db: Session, project_id: str) -> dict:
    """Return current usage for a project."""
    vols = db.scalars(
        select(Volume).where(
            Volume.project_id == project_id,
            Volume.status.not_in(["deleting", "error"]),
        )
    ).all()
    snaps_count = db.scalar(
        select(Snapshot).where(Snapshot.status.not_in(["deleting", "error"]))
    )
    return {
        "volumes": len(vols),
        "gigabytes": sum(v.size_gb for v in vols),
        "snapshots": len(db.scalars(select(Snapshot)).all()),
    }


def _quota_to_dict(q: VolumeQuota, usage: dict | None = None) -> dict:
    d = {
        "project_id": q.project_id,
        "volumes":    q.volumes,
        "gigabytes":  q.gigabytes,
        "snapshots":  q.snapshots,
    }
    if usage:
        d["usage"] = usage
    return d


@app.get("/os-quota-sets/{project_id}")
def get_quota(project_id: str, current: CurrentUser, db: DB, usage: bool = False):
    """Get quota limits for a project."""
    if current.get("role") != "admin" and current.get("project_id") != project_id:
        raise HTTPException(status_code=403, detail="forbidden")
    quota = db.get(VolumeQuota, project_id)
    if not quota:
        # Return defaults
        quota = VolumeQuota(project_id=project_id, volumes=10, gigabytes=1000, snapshots=10)
    u = _quota_usage(db, project_id) if usage else None
    return _quota_to_dict(quota, u)


@app.put("/os-quota-sets/{project_id}")
def update_quota(project_id: str, payload: QuotaIn, _: AdminUser, db: DB):
    """Set quota limits for a project (admin only)."""
    quota = db.get(VolumeQuota, project_id)
    if not quota:
        quota = VolumeQuota(project_id=project_id)
        db.add(quota)
    if payload.volumes is not None:
        quota.volumes = payload.volumes
    if payload.gigabytes is not None:
        quota.gigabytes = payload.gigabytes
    if payload.snapshots is not None:
        quota.snapshots = payload.snapshots
    db.commit()
    db.refresh(quota)
    return _quota_to_dict(quota)


@app.delete("/os-quota-sets/{project_id}", status_code=204)
def delete_quota(project_id: str, _: AdminUser, db: DB):
    """Reset quota to defaults by deleting the override row."""
    quota = db.get(VolumeQuota, project_id)
    if quota:
        db.delete(quota)
        db.commit()


@app.get("/os-quota-sets/{project_id}/defaults")
def get_quota_defaults(project_id: str, _: CurrentUser, db: DB):
    """Return the default quota values."""
    return {"project_id": project_id, "volumes": 10, "gigabytes": 1000, "snapshots": 10}

