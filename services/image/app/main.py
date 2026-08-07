import hashlib
import os
import sys
import uuid
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Annotated

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import Boolean, DateTime, Integer, String, Text, create_engine, inspect, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

try:
    from app.auth_middleware import CurrentUser, AdminUser, OptionalAuth
except ImportError:
    from auth_middleware import CurrentUser, AdminUser, OptionalAuth

# Load shared request_id_middleware
try:
    from shared.request_id_middleware import RequestIDMiddleware
except ImportError:
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
        from shared.request_id_middleware import RequestIDMiddleware
    except ImportError:
        RequestIDMiddleware = None

# Import shared RPC library (for image task dispatching)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
try:
    from shared.rpc import RPCClient
except ImportError:
    RPCClient = None


DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://mini:mini@postgres:5432/mini_openstack")
IMAGE_STORE_PATH = Path(os.getenv("IMAGE_STORE_PATH", "/var/lib/mini-openstack/images"))
IMAGE_STORAGE_BACKEND = os.getenv("IMAGE_STORAGE_BACKEND", "file").lower()
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "http://minio:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "mini-openstack-images")
MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"
RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://cloudx:cloudx@127.0.0.1:5672/")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def new_id() -> str:
    return str(uuid.uuid4())


class Image(Base):
    __tablename__ = "image_images"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String, unique=True, index=True)
    filename: Mapped[str] = mapped_column(String)
    storage_backend: Mapped[str] = mapped_column(String, default="file")
    object_name: Mapped[str] = mapped_column(String)
    disk_format: Mapped[str] = mapped_column(String)
    container_format: Mapped[str] = mapped_column(String)
    size: Mapped[int] = mapped_column(Integer)
    checksum: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="ACTIVE")
    visibility: Mapped[str] = mapped_column(String, default="private")
    project_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ImageTag(Base):
    __tablename__ = "image_tags"
    image_id: Mapped[str] = mapped_column(String, primary_key=True)
    tag: Mapped[str] = mapped_column(String, primary_key=True)


class ImageMember(Base):
    __tablename__ = "image_members"
    image_id:   Mapped[str] = mapped_column(String, primary_key=True)
    member_id:  Mapped[str] = mapped_column(String, primary_key=True)  # project_id
    status:     Mapped[str] = mapped_column(String, default="pending")  # pending | accepted | rejected
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ImageTask(Base):
    """Asynchronous image import task (Glance task API)."""
    __tablename__ = "image_tasks"
    id:         Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    type:       Mapped[str] = mapped_column(String, default="import")  # import
    status:     Mapped[str] = mapped_column(String, default="pending")  # pending|processing|success|failure
    input:      Mapped[str | None] = mapped_column(Text, nullable=True)   # JSON input
    result:     Mapped[str | None] = mapped_column(Text, nullable=True)   # JSON result
    message:    Mapped[str | None] = mapped_column(String, nullable=True) # error message
    owner:      Mapped[str | None] = mapped_column(String, nullable=True) # project_id
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class StorageBackend(ABC):
    name: str

    @abstractmethod
    def save_image(self, file, object_name: str) -> tuple[int, str]:
        """Persist a file-like object and return size plus SHA256 checksum."""

    @abstractmethod
    def delete_image(self, object_name: str) -> None:
        pass

    @abstractmethod
    def get_image_info(self, object_name: str) -> dict:
        pass

    @abstractmethod
    def exists(self, object_name: str) -> bool:
        pass


class FileStorageBackend(StorageBackend):
    name = "file"

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, object_name: str) -> Path:
        return self.root / object_name

    def save_image(self, file, object_name: str) -> tuple[int, str]:
        target = self.path_for(object_name)
        digest = hashlib.sha256()
        size = 0
        with target.open("wb") as out:
            while chunk := file.read(1024 * 1024):
                size += len(chunk)
                digest.update(chunk)
                out.write(chunk)
        return size, digest.hexdigest()

    def delete_image(self, object_name: str) -> None:
        path = self.path_for(object_name)
        if path.exists():
            path.unlink()

    def get_image_info(self, object_name: str) -> dict:
        path = self.path_for(object_name)
        if not path.exists():
            raise FileNotFoundError(object_name)
        return {"object_name": object_name, "path": str(path), "size": path.stat().st_size}

    def exists(self, object_name: str) -> bool:
        return self.path_for(object_name).exists()


class MinioStorageBackend(StorageBackend):
    name = "minio"

    def __init__(self):
        endpoint_url = MINIO_ENDPOINT if MINIO_ENDPOINT.startswith(("http://", "https://")) else f"http://{MINIO_ENDPOINT}"
        self.bucket = MINIO_BUCKET
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=MINIO_ACCESS_KEY,
            aws_secret_access_key=MINIO_SECRET_KEY,
            use_ssl=MINIO_SECURE,
            config=Config(signature_version="s3v4"),
        )

    def ensure_bucket(self) -> None:
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in {"404", "NoSuchBucket"}:
                self.client.create_bucket(Bucket=self.bucket)
                return
            raise

    def save_image(self, file, object_name: str) -> tuple[int, str]:
        self.ensure_bucket()
        digest = hashlib.sha256()
        size = 0
        spool = IMAGE_STORE_PATH / f".upload-{uuid.uuid4()}"
        IMAGE_STORE_PATH.mkdir(parents=True, exist_ok=True)
        try:
            with spool.open("wb") as out:
                while chunk := file.read(1024 * 1024):
                    size += len(chunk)
                    digest.update(chunk)
                    out.write(chunk)
            self.client.upload_file(str(spool), self.bucket, object_name)
        finally:
            if spool.exists():
                spool.unlink()
        return size, digest.hexdigest()

    def delete_image(self, object_name: str) -> None:
        try:
            self.client.delete_object(Bucket=self.bucket, Key=object_name)
        except ClientError as exc:
            raise HTTPException(status_code=502, detail=f"minio delete failed: {exc}") from exc

    def get_image_info(self, object_name: str) -> dict:
        try:
            response = self.client.head_object(Bucket=self.bucket, Key=object_name)
        except ClientError as exc:
            raise FileNotFoundError(object_name) from exc
        return {"object_name": object_name, "bucket": self.bucket, "size": response.get("ContentLength", 0)}

    def exists(self, object_name: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=object_name)
            return True
        except ClientError:
            return False


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


DB = Annotated[Session, Depends(get_db)]
app = FastAPI(title="cloudx image-api", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
if RequestIDMiddleware:
    app.add_middleware(RequestIDMiddleware)

# RPC client (lazy init)
_rpc: "RPCClient | None" = None

def _get_rpc():
    global _rpc
    if _rpc is None and RPCClient:
        try:
            _rpc = RPCClient(RABBITMQ_URL)
        except Exception:
            pass
    return _rpc


class ImageImportIn(BaseModel):
    name: str
    path: str
    disk_format: str = "qcow2"
    container_format: str = "bare"
    visibility: str = "private"


@app.on_event("startup")
def startup():
    IMAGE_STORE_PATH.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)
    ensure_columns()


def ensure_columns():
    inspector = inspect(engine)
    table_names = inspector.get_table_names()
    if "image_images" in table_names:
        columns = {column["name"] for column in inspector.get_columns("image_images")}
        with engine.begin() as conn:
            if "storage_backend" not in columns:
                conn.execute(text("ALTER TABLE image_images ADD COLUMN storage_backend VARCHAR DEFAULT 'file'"))
            if "object_name" not in columns:
                conn.execute(text("ALTER TABLE image_images ADD COLUMN object_name VARCHAR"))
                conn.execute(text("UPDATE image_images SET object_name = filename WHERE object_name IS NULL"))
            if "visibility" not in columns:
                conn.execute(text("ALTER TABLE image_images ADD COLUMN visibility VARCHAR DEFAULT 'private'"))
            if "project_id" not in columns:
                conn.execute(text("ALTER TABLE image_images ADD COLUMN project_id VARCHAR"))
            conn.execute(text("UPDATE image_images SET storage_backend = 'file' WHERE storage_backend IS NULL"))
            conn.execute(text("UPDATE image_images SET visibility = 'private' WHERE visibility IS NULL"))
            conn.execute(text("UPDATE image_images SET object_name = filename WHERE object_name IS NULL"))


def storage_backend() -> StorageBackend:
    if IMAGE_STORAGE_BACKEND == "file":
        return FileStorageBackend(IMAGE_STORE_PATH)
    if IMAGE_STORAGE_BACKEND == "minio":
        return MinioStorageBackend()
    raise HTTPException(status_code=500, detail=f"unsupported IMAGE_STORAGE_BACKEND '{IMAGE_STORAGE_BACKEND}'")


def obj_to_dict(obj):
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}


def get_image(db: Session, ref: str):
    return db.get(Image, ref) or db.scalar(select(Image).where(Image.name == ref))


def require_image(db: Session, ref: str):
    image = get_image(db, ref)
    if not image:
        raise HTTPException(status_code=404, detail=f"image '{ref}' not found")
    return image


@app.get("/health")
def health():
    return {"status": "ok", "service": "image-api", "storage_backend": IMAGE_STORAGE_BACKEND}


@app.post("/images", status_code=201)
def upload_image(
    db: DB,
    current: CurrentUser,
    name: str = Form(...),
    file: UploadFile = File(...),
    disk_format: str = Form("qcow2"),
    container_format: str = Form("bare"),
    visibility: str = Form("public"),
):
    if get_image(db, name):
        raise HTTPException(status_code=409, detail="image already exists")
    image_id = new_id()
    object_name = f"{image_id}-{Path(file.filename or name).name}"
    backend = storage_backend()
    try:
        size, checksum = backend.save_image(file.file, object_name)
    except ClientError as exc:
        raise HTTPException(status_code=502, detail=f"minio upload failed: {exc}") from exc
    image = Image(
        id=image_id,
        name=name,
        filename=str(IMAGE_STORE_PATH / object_name) if backend.name == "file" else f"s3://{MINIO_BUCKET}/{object_name}",
        storage_backend=backend.name,
        object_name=object_name,
        disk_format=disk_format,
        container_format=container_format,
        size=size,
        checksum=checksum,
        status="ACTIVE",
        visibility=visibility,
        project_id=current["project_id"],
    )
    db.add(image)
    db.commit()
    db.refresh(image)
    return obj_to_dict(image)



@app.post("/images/import-path", status_code=201)
def import_image_from_path(payload: ImageImportIn, db: DB):
    if get_image(db, payload.name):
        raise HTTPException(status_code=409, detail="image already exists")
    source = Path(payload.path)
    if not source.exists() or not source.is_file():
        raise HTTPException(status_code=404, detail=f"source image path not found: {source}")
    image_id = new_id()
    object_name = f"{image_id}-{source.name}"
    backend = storage_backend()
    try:
        with source.open("rb") as handle:
            size, checksum = backend.save_image(handle, object_name)
    except ClientError as exc:
        raise HTTPException(status_code=502, detail=f"image import failed: {exc}") from exc
    image = Image(
        id=image_id,
        name=payload.name,
        filename=str(IMAGE_STORE_PATH / object_name) if backend.name == "file" else f"s3://{MINIO_BUCKET}/{object_name}",
        storage_backend=backend.name,
        object_name=object_name,
        disk_format=payload.disk_format,
        container_format=payload.container_format,
        size=size,
        checksum=checksum,
        status="ACTIVE",
        visibility=payload.visibility,
    )
    db.add(image)
    db.commit()
    db.refresh(image)
    return obj_to_dict(image)


@app.get("/images")
def list_images(current: CurrentUser, db: DB,
                visibility: str | None = None,
                status: str | None = None,
                limit: int = 50):
    q = select(Image)
    if visibility:
        q = q.where(Image.visibility == visibility)
    elif current.get("role") != "admin":
        # Non-admin sees: public images + own private images
        q = q.where(
            (Image.visibility == "public") |
            (Image.project_id == current["project_id"])
        )
    if status:
        q = q.where(Image.status == status)
    q = q.order_by(Image.created_at).limit(limit)
    return {"items": [obj_to_dict(i) for i in db.scalars(q).all()]}


@app.get("/images/{ref}")
def show_image(ref: str, db: DB):
    return obj_to_dict(require_image(db, ref))


@app.get("/images/{ref}/download")
def download_image(ref: str, db: DB):
    image = require_image(db, ref)
    if image.storage_backend == "minio":
        backend = MinioStorageBackend()
        try:
            response = backend.client.get_object(Bucket=backend.bucket, Key=image.object_name)
        except ClientError as exc:
            raise HTTPException(status_code=502, detail=f"minio download failed: {exc}") from exc
        body = response["Body"]

        def stream():
            try:
                for chunk in body.iter_chunks(chunk_size=1024 * 1024):
                    if chunk:
                        yield chunk
            finally:
                body.close()

        return StreamingResponse(stream(), media_type="application/octet-stream", headers={"Content-Length": str(image.size)})
    path = FileStorageBackend(IMAGE_STORE_PATH).path_for(image.object_name or Path(image.filename).name)
    if not path.exists():
        raise HTTPException(status_code=404, detail="image file not found")
    return FileResponse(path, media_type="application/octet-stream", filename=Path(image.filename).name)


@app.delete("/images/{ref}")
def delete_image(ref: str, _: CurrentUser, db: DB):
    image = require_image(db, ref)
    if image.storage_backend == "minio":
        MinioStorageBackend().delete_image(image.object_name)
    else:
        FileStorageBackend(IMAGE_STORE_PATH).delete_image(image.object_name or Path(image.filename).name)
    db.delete(image)
    db.commit()
    return {"status": "deleted", "id": image.id, "name": image.name}


@app.post("/images/{ref}/deactivate")
def deactivate_image(ref: str, _: CurrentUser, db: DB):
    image = require_image(db, ref)
    image.status = "deactivated"
    db.commit()
    return obj_to_dict(image)


@app.post("/images/{ref}/reactivate")
def reactivate_image(ref: str, _: CurrentUser, db: DB):
    image = require_image(db, ref)
    image.status = "ACTIVE"
    db.commit()
    return obj_to_dict(image)


@app.patch("/images/{ref}")
def update_image(ref: str, payload: dict, _: CurrentUser, db: DB):
    """Partially update image attributes (visibility, name, status, etc.). Also called by image-worker."""
    image = require_image(db, ref)
    allowed = {"visibility", "name", "disk_format", "container_format",
               "status", "size", "checksum", "object_name", "filename", "storage_backend"}
    for key, value in payload.items():
        if key in allowed:
            setattr(image, key, value)
    db.commit()
    return obj_to_dict(image)


# ── Image Tags ─────────────────────────────────────────────────────────────────

@app.get("/images/{ref}/tags")
def list_image_tags(ref: str, db: DB):
    image = require_image(db, ref)
    tags = db.scalars(select(ImageTag).where(ImageTag.image_id == image.id)).all()
    return {"tags": [t.tag for t in tags]}


@app.put("/images/{ref}/tags/{tag}")
def add_image_tag(ref: str, tag: str, _: CurrentUser, db: DB):
    image = require_image(db, ref)
    existing = db.get(ImageTag, (image.id, tag))
    if not existing:
        db.add(ImageTag(image_id=image.id, tag=tag))
        db.commit()
    return {"image": image.name, "tag": tag}


@app.delete("/images/{ref}/tags/{tag}", status_code=204)
def delete_image_tag(ref: str, tag: str, _: CurrentUser, db: DB):
    image = require_image(db, ref)
    row = db.get(ImageTag, (image.id, tag))
    if row:
        db.delete(row)
        db.commit()


# ── Image Members (sharing) ────────────────────────────────────────────────────

class ImageMemberIn(BaseModel):
    member: str


class ImageMemberStatusIn(BaseModel):
    status: str  # pending | accepted | rejected


@app.get("/images/{ref}/members")
def list_image_members(ref: str, _: CurrentUser, db: DB):
    image = require_image(db, ref)
    members = db.scalars(select(ImageMember).where(ImageMember.image_id == image.id)).all()
    return {"members": [
        {"image_id": m.image_id, "member_id": m.member_id, "status": m.status, "created_at": m.created_at.isoformat()}
        for m in members
    ]}


@app.post("/images/{ref}/members", status_code=201)
def share_image(ref: str, payload: ImageMemberIn, _: CurrentUser, db: DB):
    image = require_image(db, ref)
    existing = db.get(ImageMember, (image.id, payload.member))
    if not existing:
        existing = ImageMember(image_id=image.id, member_id=payload.member, status="pending")
        db.add(existing)
        db.commit()
    return {"image_id": image.id, "member_id": existing.member_id, "status": existing.status}


@app.put("/images/{ref}/members/{member_id}")
def update_image_member_status(ref: str, member_id: str, payload: ImageMemberStatusIn, current: CurrentUser, db: DB):
    image = require_image(db, ref)
    member = db.get(ImageMember, (image.id, member_id))
    if not member:
        raise HTTPException(status_code=404, detail="member not found")
    # Only the target member project can accept/reject
    if current.get("project_id") != member_id and current.get("role") != "admin":
        raise HTTPException(status_code=403, detail="only the member project can accept or reject")
    if payload.status not in ("accepted", "rejected", "pending"):
        raise HTTPException(status_code=400, detail="status must be accepted, rejected, or pending")
    member.status = payload.status
    db.commit()
    return {"image_id": image.id, "member_id": member.member_id, "status": member.status}


@app.delete("/images/{ref}/members/{member_id}", status_code=204)
def remove_image_member(ref: str, member_id: str, _: CurrentUser, db: DB):
    image = require_image(db, ref)
    member = db.get(ImageMember, (image.id, member_id))
    if member:
        db.delete(member)
        db.commit()


# ── Glance Task API (async import) ────────────────────────────────────────────

import json as _json


class TaskIn(BaseModel):
    type: str = "import"   # import
    input: dict            # {import_from, import_from_format, image_properties}


class TaskPatchIn(BaseModel):
    status: str | None = None
    message: str | None = None
    result: dict | None = None


def _task_to_dict(task: ImageTask) -> dict:
    return {
        "id": task.id,
        "type": task.type,
        "status": task.status,
        "input": _json.loads(task.input) if task.input else {},
        "result": _json.loads(task.result) if task.result else None,
        "message": task.message,
        "owner": task.owner,
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat(),
    }


@app.post("/v2/tasks", status_code=201)
def create_task(payload: TaskIn, current: CurrentUser, db: DB):
    """
    Create an asynchronous image import task.

    Expected input:
      {
        "type": "import",
        "input": {
          "import_from": "http://...",
          "import_from_format": "qcow2",
          "image_properties": {"name": "...", "disk_format": "qcow2", ...}
        }
      }
    """
    if payload.type != "import":
        raise HTTPException(status_code=400, detail="only 'import' task type is supported")

    task_input = payload.input
    import_from = task_input.get("import_from", "")
    import_format = task_input.get("import_from_format", "qcow2")
    image_props = task_input.get("image_properties", {})

    if not import_from:
        raise HTTPException(status_code=400, detail="input.import_from is required")

    image_name = image_props.get("name") or f"imported-{new_id()[:8]}"

    # Pre-create image record in 'importing' status
    image_id = new_id()
    image = Image(
        id=image_id,
        name=image_name,
        filename=f"{image_id}.{import_format}",
        storage_backend=IMAGE_STORAGE_BACKEND,
        object_name=f"{image_id}.{import_format}",
        disk_format=image_props.get("disk_format", import_format),
        container_format=image_props.get("container_format", "bare"),
        size=0,
        checksum="",
        status="importing",
        visibility=image_props.get("visibility", "private"),
        project_id=current["project_id"],
    )
    db.add(image)
    db.flush()

    # Create task record
    task = ImageTask(
        type="import",
        status="pending",
        input=_json.dumps(task_input),
        owner=current["project_id"],
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    # Dispatch to image-worker via RabbitMQ (fire-and-forget)
    rpc = _get_rpc()
    if rpc:
        try:
            rpc.cast("image", "import_image_task", {
                "task_id": task.id,
                "image_id": image_id,
                "import_from": import_from,
                "image_properties": {**image_props, "disk_format": import_format},
            })
        except Exception as exc:
            # Non-fatal — task can be retried
            import logging
            logging.getLogger("image-api").warning("Failed to dispatch task to image-worker: %s", exc)
    else:
        import logging
        logging.getLogger("image-api").warning("RPC not available — image-worker task will not be dispatched")

    return _task_to_dict(task)


@app.get("/v2/tasks/{task_id}")
def get_task(task_id: str, current: CurrentUser, db: DB):
    task = db.get(ImageTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    if current.get("role") != "admin" and task.owner != current["project_id"]:
        raise HTTPException(status_code=403, detail="access denied")
    return _task_to_dict(task)


@app.get("/v2/tasks")
def list_tasks(current: CurrentUser, db: DB):
    q = select(ImageTask)
    if current.get("role") != "admin":
        q = q.where(ImageTask.owner == current["project_id"])
    tasks = db.scalars(q.order_by(ImageTask.created_at.desc())).all()
    return {"tasks": [_task_to_dict(t) for t in tasks]}


@app.patch("/v2/tasks/{task_id}")
def update_task(task_id: str, payload: TaskPatchIn, db: DB):
    """Internal endpoint called by image-worker to update task status."""
    task = db.get(ImageTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    if payload.status:
        task.status = payload.status
    if payload.message:
        task.message = payload.message
    if payload.result:
        task.result = _json.dumps(payload.result)
    task.updated_at = datetime.utcnow()
    db.commit()
    return _task_to_dict(task)


# ── API Version Discovery ────────────────────────────────────────────────────────

@app.get("/v2")
def glance_v2_root():
    """Glance v2 API version discovery."""
    return {
        "version": {
            "id": "v2.9",
            "status": "CURRENT",
            "links": [{"rel": "self", "href": "/v2"}],
        }
    }


@app.get("/")
def image_root():
    """Image API root — version discovery."""
    return {
        "versions": [
            {"id": "v2.9", "status": "CURRENT", "links": [{"rel": "self", "href": "/v2"}]},
        ]
    }


