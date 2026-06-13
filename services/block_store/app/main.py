"""
cloudx block-store-api

Supported backends: LVM (block device) and/or Ceph RBD (network disk).
File/qcow2 backend has been removed.

Configuration:
  STORAGE_LVM_DRIVER=true|false      — enable LVM backend
  STORAGE_CEPH_RBD_DRIVER=true|false — enable Ceph RBD backend
  LVM_PV_NAME=/dev/sdb,/dev/sdc     — PVs for auto VG creation (LVM only)
  LVM_VG_NAME=vg-cloudx              — VG name to create/use (LVM only)
  CEPH_POOL=volumes                  — Ceph pool (Ceph only)
  CEPH_CONF=/etc/ceph/ceph.conf
  CEPH_KEYRING=                      — optional

At least one driver must be enabled, otherwise the service refuses to start.
"""

import logging
import math
import os
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
    from app.drivers import lvm, ceph
except ImportError:
    from auth_middleware import CurrentUser, AdminUser, OptionalAuth
    from drivers import lvm, ceph

log = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────────────

DATABASE_URL   = os.getenv("DATABASE_URL", "postgresql+psycopg://mini:mini@postgres:5432/mini_openstack")
COMPUTE_API_URL = os.getenv("COMPUTE_API_URL", "http://compute-api:8000").rstrip("/")
IMAGE_API_URL   = os.getenv("IMAGE_API_URL", "http://image-api:8000").rstrip("/")

STORAGE_LVM_DRIVER      = os.getenv("STORAGE_LVM_DRIVER", "false").lower() == "true"
STORAGE_CEPH_RBD_DRIVER = os.getenv("STORAGE_CEPH_RBD_DRIVER", "false").lower() == "true"

LVM_VG_NAME  = os.getenv("LVM_VG_NAME", "")
LVM_PV_NAME  = os.getenv("LVM_PV_NAME", "")
CEPH_POOL    = os.getenv("CEPH_POOL", "")
CEPH_CONF    = os.getenv("CEPH_CONF", "/etc/ceph/ceph.conf")

# ── Database ───────────────────────────────────────────────────────────────────

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def new_id() -> str:
    return str(uuid.uuid4())


# ── ORM models ─────────────────────────────────────────────────────────────────

# Valid disk_type values (informational — not enforced at hardware level)
DISK_TYPES = ("sata_ssd", "sas_ssd", "nvme_ssd", "sata_hdd", "sas_hdd")


class VolumeType(Base):
    """Performance profile for volumes — analogous to AWS EBS volume types.

    disk_type   : hardware tier hint (sata_ssd, sas_ssd, nvme_ssd, sata_hdd, sas_hdd)
    backend     : storage infrastructure to use (lvm | ceph)
    max_iops    : informational upper bound IOPS (not enforced by hypervisor)
    max_throughput : informational upper bound MB/s (not enforced by hypervisor)
    """
    __tablename__ = "block_store_volume_types"
    id:             Mapped[str]      = mapped_column(String, primary_key=True, default=new_id)
    name:           Mapped[str]      = mapped_column(String, unique=True, index=True)
    backend:        Mapped[str]      = mapped_column(String, default="lvm")    # lvm | ceph
    disk_type:      Mapped[str]      = mapped_column(String, default="sata_ssd")  # informational
    description:    Mapped[str|None] = mapped_column(String, nullable=True)
    max_iops:       Mapped[int|None] = mapped_column(Integer, nullable=True)    # informational
    max_throughput: Mapped[int|None] = mapped_column(Integer, nullable=True)    # MB/s, informational
    is_public:      Mapped[bool]     = mapped_column(Boolean, default=True)
    created_at:     Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Volume(Base):
    __tablename__ = "block_store_volumes"

    id:                    Mapped[str]      = mapped_column(String, primary_key=True, default=new_id)
    name:                  Mapped[str]      = mapped_column(String, unique=True, index=True)
    size_gb:               Mapped[int]      = mapped_column(Integer)
    status:                Mapped[str]      = mapped_column(String, default="available")
    attached_to_server_id: Mapped[str|None] = mapped_column(String, nullable=True)
    path:                  Mapped[str]      = mapped_column(String)   # /dev/VG/LV  or  rbd:pool/image
    backend:               Mapped[str|None] = mapped_column(String, nullable=True, default="lvm")
    bootable:              Mapped[bool]     = mapped_column(Boolean, default=False)
    image_id:              Mapped[str|None] = mapped_column(String, nullable=True)
    snapshot_id:           Mapped[str|None] = mapped_column(String, nullable=True)
    source_volume_id:      Mapped[str|None] = mapped_column(String, nullable=True)
    source_type:           Mapped[str|None] = mapped_column(String, nullable=True)
    target_device:         Mapped[str|None] = mapped_column(String, nullable=True)
    project_id:            Mapped[str|None] = mapped_column(String, nullable=True, index=True)
    volume_type:           Mapped[str|None] = mapped_column(String, nullable=True, default="lvm")
    created_at:            Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Snapshot(Base):
    __tablename__ = "block_store_snapshots"

    id:         Mapped[str]      = mapped_column(String, primary_key=True, default=new_id)
    name:       Mapped[str]      = mapped_column(String, unique=True, index=True)
    volume_id:  Mapped[str]      = mapped_column(String)
    size_gb:    Mapped[int]      = mapped_column(Integer)
    status:     Mapped[str]      = mapped_column(String, default="available")
    path:       Mapped[str]      = mapped_column(String)  # /dev/VG/snap or rbd:pool/image@snap
    backend:    Mapped[str|None] = mapped_column(String, nullable=True, default="lvm")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ── Pydantic schemas ───────────────────────────────────────────────────────────

class VolumeIn(BaseModel):
    name:          str
    size_gb:       int | None = Field(None, gt=0)
    size:          int | None = None    # alias — CLI sends 'size'
    image:         str | None = None
    image_id:      str | None = None
    snapshot:      str | None = None
    source_volume: str | None = None
    bootable:      bool = False
    volume_type:   str | None = None   # volume type name; None → auto-select first available


class VolumeInternalIn(BaseModel):
    name:             str
    size_gb:          int = Field(gt=0)
    image_id:         str | None = None
    image_ref:        str | None = None
    snapshot_id:      str | None = None
    source_volume_id: str | None = None
    bootable:         bool = False
    source_type:      str | None = None
    volume_type:      str | None = None  # type name or None → auto-select


class AttachIn(BaseModel):
    server: str


class SnapshotIn(BaseModel):
    name: str


class InternalAttachIn(BaseModel):
    server_id:     str
    target_device: str | None = None


# ── FastAPI app ────────────────────────────────────────────────────────────────

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


DB = Annotated[Session, Depends(get_db)]
app = FastAPI(title="cloudx block-store-api", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Startup ────────────────────────────────────────────────────────────────────

@app.on_event("startup")
def startup():
    # 1. Validate at least one driver is enabled
    if not STORAGE_LVM_DRIVER and not STORAGE_CEPH_RBD_DRIVER:
        raise RuntimeError(
            "No storage driver is enabled! "
            "Set STORAGE_LVM_DRIVER=true and/or STORAGE_CEPH_RBD_DRIVER=true in .env"
        )

    # 2. Create DB tables
    Base.metadata.create_all(bind=engine)
    ensure_columns()

    # 3. Initialize LVM: auto-create VG from PVs if needed
    if STORAGE_LVM_DRIVER:
        if not LVM_VG_NAME:
            raise RuntimeError("STORAGE_LVM_DRIVER=true but LVM_VG_NAME is not set")
        if not LVM_PV_NAME:
            raise RuntimeError("STORAGE_LVM_DRIVER=true but LVM_PV_NAME is not set")
        try:
            lvm.ensure_vg()
            log.info("LVM VG '%s' is ready", LVM_VG_NAME)
        except Exception as exc:
            log.error("LVM VG initialization failed: %s", exc)
            raise RuntimeError(f"LVM VG initialization failed: {exc}") from exc

    # 4. Validate Ceph connectivity (light check)
    if STORAGE_CEPH_RBD_DRIVER:
        if not CEPH_POOL:
            raise RuntimeError("STORAGE_CEPH_RBD_DRIVER=true but CEPH_POOL is not set")
        log.info("Ceph RBD driver enabled, pool='%s'", CEPH_POOL)

    # 5. Volume types are NOT auto-bootstrapped.
    # Admin must create volume types manually via: cloudx volume-type create
    log.info("Block-store-api ready. No volume types are pre-created — admin must add them.")


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
            # Remove stale auto-bootstrapped 'lvm' and 'ceph' types that have
            # no meaningful disk_type — they were created by the old bootstrap logic.
            # Only remove if disk_type is NULL (meaning created before migration).
            conn.execute(text(
                "DELETE FROM block_store_volume_types "
                "WHERE name IN ('lvm', 'ceph') AND disk_type IS NULL"
            ))


# ── Helpers ────────────────────────────────────────────────────────────────────

def obj_to_dict(obj):
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}


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
    """Resolve a volume type name to the VolumeType object.

    - If type_name is given: look up by name, raise 400 if not found.
    - If type_name is None: take the first available type ordered by name.
    - If no types exist at all: raise 400 with a clear admin instruction.
    """
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

    # No type specified — pick the first one alphabetically
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


# ── Image download helper ──────────────────────────────────────────────────────

def image_get(ref: str) -> dict:
    try:
        response = requests.get(f"{IMAGE_API_URL}/images/{ref}", timeout=15)
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"image-api lookup failed: {exc}") from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=_response_detail(response))
    return response.json()


def download_image_to_tmp(image_ref: str) -> tuple[str, dict]:
    """Download image to a temporary file. Returns (tmp_path, image_dict)."""
    import tempfile
    image = image_get(image_ref)
    tmp = tempfile.NamedTemporaryFile(prefix=".img-", delete=False)
    try:
        with requests.get(f"{IMAGE_API_URL}/images/{image['id']}/download",
                          timeout=300, stream=True) as response:
            if response.status_code >= 400:
                raise HTTPException(status_code=response.status_code, detail=_response_detail(response))
            for chunk in response.iter_content(chunk_size=4 * 1024 * 1024):
                if chunk:
                    tmp.write(chunk)
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"image download failed: {exc}") from exc
    finally:
        tmp.close()
    return tmp.name, image


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


def compute_post(path: str, payload: dict) -> dict:
    try:
        response = requests.post(f"{COMPUTE_API_URL}{path}", json=payload, timeout=60)
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=_response_detail(response))
    return response.json()


# ── Volume creation (backend-routed) ──────────────────────────────────────────

def _lv_name(volume_id: str) -> str:
    """Generate a safe LV/RBD image name from a volume UUID."""
    return f"vol-{volume_id.replace('-', '')[:24]}"


def _snap_lv_name(snapshot_id: str) -> str:
    return f"snap-{snapshot_id.replace('-', '')[:22]}"


def _create_backend_volume(
    volume_id: str,
    backend: str,
    size_gb: int,
    image_ref: str | None = None,
    snapshot: Snapshot | None = None,
    source_volume: Volume | None = None,
) -> str:
    """
    Create the actual block/network storage and return its path.

    Path conventions:
      LVM  → /dev/{VG}/{lv_name}
      Ceph → rbd:{pool}/{image_name}
    """
    lv = _lv_name(volume_id)

    # ── source: image ───────────────────────────────────────────────────────
    if image_ref:
        tmp_path, _ = download_image_to_tmp(image_ref)
        try:
            if backend == "lvm":
                return lvm.create_from_source(lv, tmp_path, size_gb)
            else:
                return ceph.create_from_source(lv, tmp_path, size_gb)
        finally:
            import os as _os
            try:
                _os.unlink(tmp_path)
            except Exception:
                pass

    # ── source: snapshot ────────────────────────────────────────────────────
    if snapshot:
        snap_lv = _snap_lv_name(snapshot.id)
        if backend == "lvm":
            # Restore from LVM snapshot: create new LV and dd from snapshot device
            snap_dev = snapshot.path    # e.g. /dev/vg-cloudx/snap-xxx
            dev = lvm.create(lv, size_gb)
            import subprocess as _sp
            _sp.run(["dd", f"if={snap_dev}", f"of={dev}", "bs=4M", "status=none"], check=False)
            return dev
        else:
            # Ceph clone from snapshot
            snap_img, _, snap_name = snapshot.path.partition("@")
            snap_img = snap_img.replace("rbd:", "").split("/", 1)[-1]  # pool/image → image
            return ceph.clone(snap_img, snap_name, lv)

    # ── source: volume clone ────────────────────────────────────────────────
    if source_volume:
        if backend == "lvm":
            src_dev = source_volume.path
            dev = lvm.create(lv, size_gb)
            import subprocess as _sp
            _sp.run(["dd", f"if={src_dev}", f"of={dev}", "bs=4M", "status=none"], check=False)
            return dev
        else:
            # Ceph: snapshot source then clone
            snap_name = f"clone-src-{_snap_lv_name(volume_id)}"
            src_img = source_volume.path.replace("rbd:", "").split("/", 1)[-1]
            return ceph.clone(src_img, snap_name, lv)

    # ── blank volume ────────────────────────────────────────────────────────
    if backend == "lvm":
        return lvm.create(lv, size_gb)
    else:
        return ceph.create(lv, size_gb)


def create_volume_record(db: Session, payload: VolumeInternalIn) -> Volume:
    if get_volume(db, payload.name):
        raise HTTPException(status_code=409, detail="volume already exists")

    source_count = sum(bool(x) for x in (
        payload.image_ref or payload.image_id,
        payload.snapshot_id,
        payload.source_volume_id,
    ))
    if source_count > 1:
        raise HTTPException(status_code=400, detail="choose only one source: image, snapshot, or source_volume")

    # Resolve volume type
    vt = _resolve_volume_type(db, payload.volume_type)
    _validate_backend(vt)
    backend = vt.backend

    volume_id   = new_id()
    image_id    = None
    snapshot_id = None
    source_volume_id = None
    source_type = payload.source_type
    bootable    = payload.bootable
    snapshot    = None
    source_vol  = None

    image_ref = payload.image_ref or payload.image_id
    if image_ref:
        image_meta = image_get(image_ref)
        image_id   = image_meta["id"]
        image_ref  = image_ref   # keep original ref for download
        source_type = "image"
        bootable    = True
    elif payload.snapshot_id:
        snapshot = require_snapshot(db, payload.snapshot_id)
        snapshot_id = snapshot.id
        source_type = "snapshot"
        # Inherit backend from snapshot
        backend = snapshot.backend or backend
    elif payload.source_volume_id:
        source_vol = require_volume(db, payload.source_volume_id)
        source_volume_id = source_vol.id
        source_type = "volume"
        bootable    = source_vol.bootable
        backend     = source_vol.backend or backend
    else:
        source_type = source_type or "blank"

    # Actually create the block storage
    try:
        path = _create_backend_volume(
            volume_id=volume_id,
            backend=backend,
            size_gb=payload.size_gb,
            image_ref=image_ref if (payload.image_ref or payload.image_id) else None,
            snapshot=snapshot,
            source_volume=source_vol,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"storage creation failed: {exc}") from exc

    volume = Volume(
        id=volume_id,
        name=payload.name,
        size_gb=payload.size_gb,
        status="available",
        path=path,
        backend=backend,
        bootable=bootable,
        image_id=image_id,
        snapshot_id=snapshot_id,
        source_volume_id=source_volume_id,
        source_type=source_type,
        volume_type=vt.name,
    )
    db.add(volume)
    db.commit()
    db.refresh(volume)
    return volume


def _delete_backend_volume(volume: Volume) -> None:
    """Delete the actual block/network storage."""
    lv = _lv_name(volume.id)
    try:
        if volume.backend == "lvm":
            lvm.delete(lv)
        elif volume.backend == "ceph":
            ceph.delete(lv)
    except Exception as exc:
        log.warning("Backend delete failed for volume %s (%s): %s", volume.name, volume.backend, exc)


def _delete_backend_snapshot(snapshot: Snapshot) -> None:
    lv = _snap_lv_name(snapshot.id)
    try:
        if snapshot.backend == "lvm":
            lvm.delete(lv)
        elif snapshot.backend == "ceph":
            ceph.delete(lv)
    except Exception as exc:
        log.warning("Backend delete failed for snapshot %s: %s", snapshot.name, exc)


# ── API Routes ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "block-store-api",
        "drivers": {
            "lvm":  STORAGE_LVM_DRIVER,
            "ceph": STORAGE_CEPH_RBD_DRIVER,
        },
        "lvm_vg":   LVM_VG_NAME if STORAGE_LVM_DRIVER else None,
        "ceph_pool": CEPH_POOL if STORAGE_CEPH_RBD_DRIVER else None,
    }


@app.post("/volumes", status_code=201)
def create_volume(payload: VolumeIn, current: CurrentUser, db: DB):
    size = payload.size_gb or payload.size
    if not size:
        raise HTTPException(status_code=400, detail="size_gb is required")
    internal = VolumeInternalIn(
        name=payload.name,
        size_gb=size,
        image_ref=payload.image or payload.image_id,
        snapshot_id=payload.snapshot,
        source_volume_id=payload.source_volume,
        bootable=payload.bootable,
        volume_type=payload.volume_type,
    )
    vol = create_volume_record(db, internal)
    vol.project_id = current["project_id"]
    db.commit()
    db.refresh(vol)
    return obj_to_dict(vol)


@app.post("/internal/volumes", status_code=201)
def create_volume_internal(payload: VolumeInternalIn, db: DB):
    return obj_to_dict(create_volume_record(db, payload))


@app.get("/volumes")
def list_volumes(current: CurrentUser, db: DB):
    q = select(Volume)
    if current.get("role") != "admin":
        q = q.where(Volume.project_id == current["project_id"])
    return {"items": [obj_to_dict(v) for v in db.scalars(q.order_by(Volume.created_at)).all()]}


@app.get("/volumes/{ref}")
def show_volume(ref: str, _: CurrentUser, db: DB):
    return obj_to_dict(require_volume(db, ref))


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


@app.post("/internal/volumes/{ref}/attach-server")
def internal_attach_server(ref: str, payload: InternalAttachIn, db: DB):
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
    volume = require_volume(db, ref)
    volume.attached_to_server_id = None
    volume.target_device = None
    volume.status = "available"
    db.commit()
    db.refresh(volume)
    return obj_to_dict(volume)


@app.post("/volumes/{ref}/attach")
def attach_volume(ref: str, payload: AttachIn, db: DB):
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
    compute_post(
        f"/servers/{server['id']}/volumes/attach",
        {
            "volume_id":   volume.id,
            "volume_name": volume.name,
            "path":        volume.path,
            "backend":     volume.backend or "lvm",
            "target_dev":  target_device,
        },
    )
    volume.attached_to_server_id = server["id"]
    volume.target_device = target_device
    volume.status = "in-use"
    db.commit()
    db.refresh(volume)
    return obj_to_dict(volume)


@app.post("/volumes/{ref}/detach")
def detach_volume(ref: str, db: DB):
    volume = require_volume(db, ref)
    if volume.status != "in-use" or not volume.attached_to_server_id:
        raise HTTPException(status_code=409, detail="volume is not attached")
    compute_post(
        f"/servers/{volume.attached_to_server_id}/volumes/detach",
        {
            "volume_id":   volume.id,
            "volume_name": volume.name,
            "path":        volume.path,
            "backend":     volume.backend or "lvm",
            "target_dev":  volume.target_device,
        },
    )
    volume.attached_to_server_id = None
    volume.target_device = None
    volume.status = "available"
    db.commit()
    db.refresh(volume)
    return obj_to_dict(volume)


@app.post("/volumes/{ref}/force-detach")
def force_detach_volume(ref: str, db: DB):
    volume = require_volume(db, ref)
    volume.attached_to_server_id = None
    volume.target_device = None
    volume.status = "available"
    db.commit()
    db.refresh(volume)
    return obj_to_dict(volume)


@app.post("/servers/{server_id}/volumes/detach-all")
def detach_server_volumes(server_id: str, db: DB):
    volumes = db.scalars(select(Volume).where(Volume.attached_to_server_id == server_id)).all()
    detached = []
    for volume in volumes:
        volume.attached_to_server_id = None
        volume.target_device = None
        volume.status = "available"
        detached.append(obj_to_dict(volume))
    db.commit()
    return {"server_id": server_id, "detached": detached}


@app.delete("/volumes/{ref}")
def delete_volume(ref: str, _: CurrentUser, db: DB):
    volume = require_volume(db, ref)
    if volume.status == "in-use":
        raise HTTPException(status_code=409, detail="detach volume before delete")
    _delete_backend_volume(volume)
    db.delete(volume)
    db.commit()
    return {"status": "deleted", "id": volume.id, "name": volume.name}


@app.post("/volumes/{ref}/extend")
def extend_volume(ref: str, payload: dict, _: CurrentUser, db: DB):
    volume = require_volume(db, ref)
    if volume.status == "in-use":
        raise HTTPException(status_code=409, detail="cannot extend an attached volume")
    new_size = payload.get("new_size") or payload.get("new_size_gb")
    if not new_size or new_size <= volume.size_gb:
        raise HTTPException(status_code=400, detail=f"new_size must be > current size ({volume.size_gb} GB)")
    lv = _lv_name(volume.id)
    try:
        if volume.backend == "lvm":
            lvm.extend(lv, new_size)
        elif volume.backend == "ceph":
            ceph.extend(lv, new_size)
        else:
            raise HTTPException(status_code=400, detail=f"Unknown backend: {volume.backend}")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"extend failed: {exc}") from exc
    volume.size_gb = new_size
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
    lv          = _lv_name(source.id)
    snap_lv     = _snap_lv_name(snapshot_id)
    # Snapshot COW size = 20% of volume size, minimum 1 GB
    cow_gb = max(1, math.ceil(source.size_gb * 0.2))

    try:
        if source.backend == "lvm":
            path = lvm.snapshot(lv, snap_lv, cow_gb)
        elif source.backend == "ceph":
            path = ceph.snapshot(lv, snap_lv, cow_gb)
        else:
            raise HTTPException(status_code=400, detail=f"Unknown backend: {source.backend}")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"snapshot failed: {exc}") from exc

    snap = Snapshot(
        id=snapshot_id,
        name=snap_name,
        volume_id=source.id,
        size_gb=source.size_gb,
        status="available",
        path=path,
        backend=source.backend,
    )
    db.add(snap)
    db.commit()
    db.refresh(snap)
    return obj_to_dict(snap)


@app.get("/snapshots")
def list_snapshots(_: CurrentUser, db: DB):
    return {"items": [obj_to_dict(s) for s in db.scalars(select(Snapshot).order_by(Snapshot.created_at)).all()]}


@app.get("/snapshots/{ref}")
def show_snapshot(ref: str, _: CurrentUser, db: DB):
    return obj_to_dict(require_snapshot(db, ref))


@app.delete("/snapshots/{ref}")
def delete_snapshot(ref: str, _: CurrentUser, db: DB):
    snapshot = require_snapshot(db, ref)
    _delete_backend_snapshot(snapshot)
    db.delete(snapshot)
    db.commit()
    return {"status": "deleted", "id": snapshot.id, "name": snapshot.name}


# ── Backup ─────────────────────────────────────────────────────────────────────

@app.post("/volumes/{ref}/backup")
def backup_volume(ref: str, payload: SnapshotIn, _: CurrentUser, db: DB):
    """Create a snapshot as backup (re-uses snapshot logic with backup_ prefix)."""
    volume = require_volume(db, ref)
    backup_id = new_id()
    snap_lv   = f"bkp-{backup_id.replace('-', '')[:22]}"
    lv        = _lv_name(volume.id)
    cow_gb    = max(1, math.ceil(volume.size_gb * 0.2))

    try:
        if volume.backend == "lvm":
            path = lvm.snapshot(lv, snap_lv, cow_gb)
        elif volume.backend == "ceph":
            path = ceph.snapshot(lv, snap_lv, cow_gb)
        else:
            raise HTTPException(status_code=400, detail=f"Unknown backend: {volume.backend}")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"backup failed: {exc}") from exc

    return {
        "id":        backup_id,
        "name":      payload.name,
        "volume_id": volume.id,
        "size_gb":   volume.size_gb,
        "path":      path,
        "backend":   volume.backend,
        "status":    "available",
    }


# ── Volume Types ───────────────────────────────────────────────────────────────
#
# Volume types are AWS EBS-style performance profiles.
# They are NOT auto-created on startup — admin must create them.
#
# Each volume type specifies:
#   name           : user-facing label  (gp2, gp3, io1, io2, sc1, st1 ...)
#   disk_type      : hardware tier hint (sata_ssd, sas_ssd, nvme_ssd, sata_hdd, sas_hdd)
#   backend        : storage driver     (lvm | ceph)
#   max_iops       : informational IOPS upper bound
#   max_throughput : informational throughput upper bound (MB/s)

class VolumeTypeIn(BaseModel):
    name:           str
    description:    str | None = None
    disk_type:      str        = "sata_ssd"   # sata_ssd|sas_ssd|nvme_ssd|sata_hdd|sas_hdd
    backend:        str        = "lvm"         # lvm | ceph
    max_iops:       int | None = None          # informational
    max_throughput: int | None = None          # MB/s, informational
    is_public:      bool       = True


def _vt_to_dict(vt: VolumeType) -> dict:
    return {c.name: getattr(vt, c.name) for c in vt.__table__.columns}


@app.post("/volume-types", status_code=201)
def create_volume_type(payload: VolumeTypeIn, _: AdminUser, db: DB):
    # Validate backend
    if payload.backend not in ("lvm", "ceph"):
        raise HTTPException(status_code=400, detail="backend must be 'lvm' or 'ceph'")
    if payload.backend == "lvm" and not STORAGE_LVM_DRIVER:
        raise HTTPException(status_code=400, detail="LVM driver is disabled (STORAGE_LVM_DRIVER=false)")
    if payload.backend == "ceph" and not STORAGE_CEPH_RBD_DRIVER:
        raise HTTPException(status_code=400, detail="Ceph driver is disabled (STORAGE_CEPH_RBD_DRIVER=false)")
    # Validate disk_type
    if payload.disk_type not in DISK_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"disk_type '{payload.disk_type}' is not valid. Choose from: {', '.join(DISK_TYPES)}"
        )
    # Duplicate check
    if db.scalar(select(VolumeType).where(VolumeType.name == payload.name)):
        raise HTTPException(status_code=409, detail=f"Volume type '{payload.name}' already exists")

    vt = VolumeType(**payload.model_dump())
    db.add(vt)
    db.commit()
    db.refresh(vt)
    return _vt_to_dict(vt)


@app.get("/volume-types")
def list_volume_types(db: DB, _: CurrentUser = None):
    types = db.scalars(select(VolumeType).order_by(VolumeType.name)).all()
    return {"items": [_vt_to_dict(vt) for vt in types]}


@app.get("/volume-types/{ref}")
def show_volume_type(ref: str, _: CurrentUser, db: DB):
    vt = db.get(VolumeType, ref) or db.scalar(select(VolumeType).where(VolumeType.name == ref))
    if not vt:
        raise HTTPException(status_code=404, detail="volume type not found")
    return _vt_to_dict(vt)


@app.patch("/volume-types/{ref}")
def update_volume_type(ref: str, payload: dict, _: AdminUser, db: DB):
    """Update description, max_iops, max_throughput, is_public of a volume type."""
    vt = db.get(VolumeType, ref) or db.scalar(select(VolumeType).where(VolumeType.name == ref))
    if not vt:
        raise HTTPException(status_code=404, detail="volume type not found")
    allowed = {"description", "max_iops", "max_throughput", "is_public"}
    for key, val in payload.items():
        if key in allowed:
            setattr(vt, key, val)
    db.commit()
    db.refresh(vt)
    return _vt_to_dict(vt)


@app.delete("/volume-types/{ref}", status_code=204)
def delete_volume_type(ref: str, _: AdminUser, db: DB):
    vt = db.get(VolumeType, ref) or db.scalar(select(VolumeType).where(VolumeType.name == ref))
    if not vt:
        raise HTTPException(status_code=404, detail="volume type not found")
    # Prevent deletion if volumes are using this type
    in_use = db.scalar(select(Volume).where(Volume.volume_type == vt.name))
    if in_use:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot delete volume type '{vt.name}': it is in use by one or more volumes"
        )
    db.delete(vt)
    db.commit()
