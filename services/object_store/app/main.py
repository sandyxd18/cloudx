"""
cloudx Object Store API (swift/s3-compatible)
==============================================

Manages buckets, objects, and bucket-types for multi-tenant object storage.
"""
from __future__ import annotations

import io
import json
import logging
import os
import uuid
from datetime import datetime
from typing import Annotated, BinaryIO

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, create_engine, text, inspect
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

DATABASE_URL = os.getenv("DATABASE_URL",
    "postgresql+psycopg://mini:mini@postgres:5432/mini_openstack")
LOG_LEVEL    = os.getenv("LOG_LEVEL", "INFO")

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(name)s] %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("object-store-api")

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

class BucketType(Base):
    __tablename__ = "object_store_bucket_types"

    id:           Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name:         Mapped[str] = mapped_column(String, unique=True, index=True)
    driver:       Mapped[str] = mapped_column(String)  # s3 | filesystem
    config:       Mapped[str] = mapped_column(String, default="{}")  # JSON
    description:  Mapped[str | None] = mapped_column(String, nullable=True)
    created_at:   Mapped[datetime] = mapped_column(DateTime, default=now)


class Bucket(Base):
    __tablename__ = "object_store_buckets"

    id:              Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name:            Mapped[str] = mapped_column(String, index=True)
    project_id:      Mapped[str] = mapped_column(String, index=True)
    bucket_type_id:  Mapped[str] = mapped_column(String, ForeignKey("object_store_bucket_types.id"))
    visibility:      Mapped[str] = mapped_column(String, default="private")  # private | public-read
    network_id:      Mapped[str | None] = mapped_column(String, nullable=True)
    status:          Mapped[str] = mapped_column(String, default="active")   # active | error | deleting
    size_bytes:      Mapped[int] = mapped_column(Integer, default=0)
    object_count:    Mapped[int] = mapped_column(Integer, default=0)
    created_at:      Mapped[datetime] = mapped_column(DateTime, default=now)


class ObjectMeta(Base):
    __tablename__ = "object_store_objects"

    id:           Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    bucket_id:    Mapped[str] = mapped_column(String, ForeignKey("object_store_buckets.id"))
    key:          Mapped[str] = mapped_column(String, index=True)
    size:         Mapped[int] = mapped_column(Integer, default=0)
    content_type: Mapped[str] = mapped_column(String, default="application/octet-stream")
    created_at:   Mapped[datetime] = mapped_column(DateTime, default=now)


# ── Schemas ───────────────────────────────────────────────────────────────────

class BucketTypeIn(BaseModel):
    name: str
    driver: str = Field(..., pattern="^(s3|filesystem)$")
    config: dict = Field(default_factory=dict)
    description: str | None = None


class BucketIn(BaseModel):
    name: str
    bucket_type_id: str | None = None
    visibility: str = Field(default="private", pattern="^(private|public-read)$")
    network_id: str | None = None


class BucketUpdateIn(BaseModel):
    visibility: str = Field(..., pattern="^(private|public-read)$")


class BucketEndpointIn(BaseModel):
    endpoint_type: str = Field(..., pattern="^(internal|public)$")
    network_id: str | None = None


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(title="cloudx object-store-api", version="1.0.0")

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
    inspector = inspect(engine)
    if "object_store_buckets" in inspector.get_table_names():
        columns = {c["name"] for c in inspector.get_columns("object_store_buckets")}
        with engine.begin() as conn:
            if "network_id" not in columns:
                conn.execute(text("ALTER TABLE object_store_buckets ADD COLUMN network_id VARCHAR"))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_driver(bucket_type: BucketType):
    """Instantiate the correct storage driver from a bucket_type record."""
    from app.drivers import get_driver
    driver_cls = get_driver(bucket_type.driver)
    config = json.loads(bucket_type.config) if bucket_type.config else {}
    return driver_cls(config)


def _bucket_to_dict(b: Bucket, bt: BucketType | None = None) -> dict:
    out = {
        "id": b.id,
        "name": b.name,
        "project_id": b.project_id,
        "bucket_type_id": b.bucket_type_id,
        "visibility": b.visibility,
        "network_id": getattr(b, "network_id", None),
        "status": b.status,
        "size_bytes": b.size_bytes,
        "object_count": b.object_count,
        "created_at": b.created_at.isoformat() if b.created_at else None,
    }
    if bt:
        out["bucket_type"] = {
            "id": bt.id,
            "name": bt.name,
            "driver": bt.driver,
        }
    return out


def _object_to_dict(o: ObjectMeta) -> dict:
    return {
        "id": o.id,
        "bucket_id": o.bucket_id,
        "key": o.key,
        "size": o.size,
        "content_type": o.content_type,
        "created_at": o.created_at.isoformat() if o.created_at else None,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Admin: Bucket Types
# ═══════════════════════════════════════════════════════════════════════════════

@app.post("/admin/bucket-types", status_code=201)
def create_bucket_type(payload: BucketTypeIn, _: AdminUser, db: DB):
    if db.query(BucketType).filter_by(name=payload.name).first():
        raise HTTPException(status_code=409, detail="bucket type already exists")
    bt = BucketType(
        name=payload.name,
        driver=payload.driver,
        config=json.dumps(payload.config),
        description=payload.description,
    )
    db.add(bt)
    db.commit()
    return {"id": bt.id, "name": bt.name, "driver": bt.driver, "config": payload.config}


@app.get("/admin/bucket-types")
def list_bucket_types(_: CurrentUser, db: DB):
    rows = db.query(BucketType).all()
    return {
        "bucket_types": [
            {"id": r.id, "name": r.name, "driver": r.driver,
             "config": json.loads(r.config) if r.config else {},
             "description": r.description}
            for r in rows
        ]
    }


@app.get("/admin/bucket-types/{ref}")
def show_bucket_type(ref: str, _: CurrentUser, db: DB):
    bt = db.query(BucketType).filter_by(id=ref).first()
    if not bt:
        raise HTTPException(status_code=404, detail="bucket type not found")
    return {
        "id": bt.id, "name": bt.name, "driver": bt.driver,
        "config": json.loads(bt.config) if bt.config else {},
        "description": bt.description,
    }


@app.delete("/admin/bucket-types/{ref}", status_code=204)
def delete_bucket_type(ref: str, _: AdminUser, db: DB):
    bt = db.query(BucketType).filter_by(id=ref).first()
    if not bt:
        raise HTTPException(status_code=404, detail="bucket type not found")
    # Prevent deletion if buckets still reference it
    if db.query(Bucket).filter_by(bucket_type_id=ref).first():
        raise HTTPException(status_code=409, detail="bucket type still in use")
    db.delete(bt)
    db.commit()
    return


# ═══════════════════════════════════════════════════════════════════════════════
# User: Buckets
# ═══════════════════════════════════════════════════════════════════════════════

@app.post("/buckets", status_code=201)
def create_bucket(payload: BucketIn, current: CurrentUser, db: DB):
    project_id = current.get("project_id") or current.get("tenant_id") or "default"
    if payload.bucket_type_id:
        bt = db.query(BucketType).filter_by(id=payload.bucket_type_id).first()
    else:
        bt = db.query(BucketType).first()
    if not bt:
        raise HTTPException(status_code=404, detail="no bucket type available")

    # Name uniqueness per project
    if db.query(Bucket).filter_by(name=payload.name, project_id=project_id).first():
        raise HTTPException(status_code=409, detail="bucket name already exists in this project")

    # Create backend bucket
    driver = _get_driver(bt)
    try:
        driver.create_bucket(payload.name)
    except Exception as exc:
        log.error("Failed to create backend bucket %s: %s", payload.name, exc)
        raise HTTPException(status_code=502, detail=f"backend driver error: {exc}")

    b = Bucket(
        name=payload.name,
        project_id=project_id,
        bucket_type_id=bt.id,
        visibility=payload.visibility,
        network_id=payload.network_id,
    )
    db.add(b)
    db.commit()
    return _bucket_to_dict(b, bt)


@app.get("/buckets")
def list_buckets(current: CurrentUser, db: DB):
    project_id = current.get("project_id") or current.get("tenant_id") or "default"
    rows = db.query(Bucket).filter_by(project_id=project_id).all()
    return {"buckets": [_bucket_to_dict(r) for r in rows]}


@app.get("/buckets/{name}")
def show_bucket(name: str, current: CurrentUser, db: DB):
    project_id = current.get("project_id") or current.get("tenant_id") or "default"
    b = db.query(Bucket).filter_by(name=name, project_id=project_id).first()
    if not b:
        raise HTTPException(status_code=404, detail="bucket not found")
    bt = db.query(BucketType).filter_by(id=b.bucket_type_id).first()
    return _bucket_to_dict(b, bt)


@app.patch("/buckets/{name}")
def update_bucket(name: str, payload: BucketUpdateIn, current: CurrentUser, db: DB):
    project_id = current.get("project_id") or current.get("tenant_id") or "default"
    b = db.query(Bucket).filter_by(name=name, project_id=project_id).first()
    if not b:
        raise HTTPException(status_code=404, detail="bucket not found")
    b.visibility = payload.visibility
    db.commit()
    bt = db.query(BucketType).filter_by(id=b.bucket_type_id).first()
    return _bucket_to_dict(b, bt)


@app.delete("/buckets/{name}")
def delete_bucket(name: str, current: CurrentUser, db: DB):
    project_id = current.get("project_id") or current.get("tenant_id") or "default"
    b = db.query(Bucket).filter_by(name=name, project_id=project_id).first()
    if not b:
        raise HTTPException(status_code=404, detail="bucket not found")

    bt = db.query(BucketType).filter_by(id=b.bucket_type_id).first()
    driver = _get_driver(bt)

    # Cascade delete all objects inside this bucket
    objs = db.query(ObjectMeta).filter_by(bucket_id=b.id).all()
    for obj in objs:
        try:
            driver.delete_object(name, obj.key)
        except Exception as exc:
            log.warning("Backend delete object %s/%s: %s (continuing)", name, obj.key, exc)
        db.delete(obj)

    try:
        driver.delete_bucket(name)
    except Exception as exc:
        log.warning("Backend delete bucket %s: %s (continuing)", name, exc)

    db.delete(b)
    db.commit()
    return {"status": "deleted", "name": name}


# ═══════════════════════════════════════════════════════════════════════════════
# User: Objects
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/buckets/{name}/objects")
def list_objects(name: str, prefix: str = "", limit: int = 1000, current: CurrentUser = None, db: DB = None):
    project_id = current.get("project_id") or current.get("tenant_id") or "default"
    b = db.query(Bucket).filter_by(name=name, project_id=project_id).first()
    if not b:
        raise HTTPException(status_code=404, detail="bucket not found")

    # Prefer DB metadata; fallback to driver list for drift
    db_objs = db.query(ObjectMeta).filter(
        ObjectMeta.bucket_id == b.id,
        ObjectMeta.key.startswith(prefix),
    ).limit(limit).all()

    return {"objects": [_object_to_dict(o) for o in db_objs]}


@app.put("/buckets/{name}/objects/{key:path}")
def put_object(name: str, key: str, file: UploadFile, current: CurrentUser, db: DB):
    project_id = current.get("project_id") or current.get("tenant_id") or "default"
    b = db.query(Bucket).filter_by(name=name, project_id=project_id).first()
    if not b:
        raise HTTPException(status_code=404, detail="bucket not found")

    bt = db.query(BucketType).filter_by(id=b.bucket_type_id).first()
    driver = _get_driver(bt)

    body = file.file
    size = 0
    if hasattr(body, "seek") and hasattr(body, "tell"):
        body.seek(0, 2)
        size = body.tell()
        body.seek(0)

    try:
        driver.put_object(name, key, body, size=size)
    except Exception as exc:
        log.error("Failed to upload object %s/%s: %s", name, key, exc)
        raise HTTPException(status_code=502, detail=f"backend upload error: {exc}")

    # Upsert DB record
    existing = db.query(ObjectMeta).filter_by(bucket_id=b.id, key=key).first()
    if existing:
        existing.size = size
        existing.content_type = file.content_type or "application/octet-stream"
    else:
        existing = ObjectMeta(
            bucket_id=b.id,
            key=key,
            size=size,
            content_type=file.content_type or "application/octet-stream",
        )
        db.add(existing)
        b.object_count += 1

    b.size_bytes += size
    db.commit()
    return _object_to_dict(existing)


@app.get("/buckets/{name}/objects/{key:path}")
def get_object(name: str, key: str, current: CurrentUser, db: DB):
    project_id = current.get("project_id") or current.get("tenant_id") or "default"
    b = db.query(Bucket).filter_by(name=name, project_id=project_id).first()
    if not b:
        raise HTTPException(status_code=404, detail="bucket not found")

    bt = db.query(BucketType).filter_by(id=b.bucket_type_id).first()
    driver = _get_driver(bt)

    try:
        data = driver.get_object(name, key)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="object not found")
    except Exception as exc:
        log.error("Failed to download object %s/%s: %s", name, key, exc)
        raise HTTPException(status_code=502, detail=f"backend download error: {exc}")

    if isinstance(data, str):
        data = data.encode("utf-8")
    return Response(content=data, media_type="application/octet-stream")


@app.delete("/buckets/{name}/objects/{key:path}")
def delete_object(name: str, key: str, current: CurrentUser, db: DB):
    project_id = current.get("project_id") or current.get("tenant_id") or "default"
    b = db.query(Bucket).filter_by(name=name, project_id=project_id).first()
    if not b:
        raise HTTPException(status_code=404, detail="bucket not found")

    bt = db.query(BucketType).filter_by(id=b.bucket_type_id).first()
    driver = _get_driver(bt)

    try:
        driver.delete_object(name, key)
    except Exception as exc:
        log.warning("Backend delete object %s/%s: %s", name, key, exc)

    existing = db.query(ObjectMeta).filter_by(bucket_id=b.id, key=key).first()
    if existing:
        b.size_bytes = max(0, b.size_bytes - existing.size)
        b.object_count = max(0, b.object_count - 1)
        db.delete(existing)
        db.commit()

    return {"status": "deleted", "bucket": name, "key": key}


# ═══════════════════════════════════════════════════════════════════════════════
# Health
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/health")
def health():
    return {"status": "ok", "service": "object-store-api"}


@app.get("/healthcheck")
def healthcheck(db: DB):
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ok", "checks": {"db": True}}
    except Exception as exc:
        return {"status": "error", "checks": {"db": False, "error": str(exc)}}
