from __future__ import annotations
import os
from shared.config import load_service_config
load_service_config("key-manager-api")

"""
cloudx Key Manager API — Barbican-like Secret Management Service
================================================================

Service Phase 7.1: Key management service for LUKS encryption keys, SSL certificates, SSH keys.
"""


import base64
import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Annotated

from cryptography.fernet import Fernet
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import (
    DateTime, Integer, String, Text,
    create_engine, select
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from shared.auth_middleware import require_auth
from shared.schema import require_schema

from shared.request_id_middleware import RequestIDMiddleware

log = logging.getLogger("key-manager-api")

DATABASE_URL = os.getenv("KEY_MANAGER_DATABASE_URL", os.getenv("DATABASE_URL", "postgresql+psycopg://cloudx:cloudx@127.0.0.1:5432/cloudx_key_manager"))
MASTER_KEY = os.environ["KEY_MANAGER_MASTER_KEY"]

# Derive a 32-byte urlsafe base64 Fernet key from MASTER_KEY
_fernet_key = base64.urlsafe_b64encode(hashlib.sha256(MASTER_KEY.encode()).digest())
_cipher = Fernet(_fernet_key)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


class Secret(Base):
    __tablename__ = "key_manager_secrets"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: f"sec-{uuid.uuid4().hex[:12]}")
    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    secret_type: Mapped[str] = mapped_column(String(32), default="symmetric")  # symmetric, passphrase, certificate, rsa, opaque
    encrypted_payload: Mapped[str] = mapped_column(Text, nullable=False)
    algorithm: Mapped[str | None] = mapped_column(String(32), nullable=True)
    bit_length: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class SecretContainer(Base):
    __tablename__ = "key_manager_containers"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: f"ctr-{uuid.uuid4().hex[:12]}")
    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    container_type: Mapped[str] = mapped_column(String(32), default="generic")  # generic, rsa, certificate
    secret_refs_json: Mapped[str] = mapped_column(Text, default="[]")
    project_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


DB = Annotated[Session, Depends(get_db)]

app = FastAPI(title="cloudx Key Manager API", version="1.0.0")

if RequestIDMiddleware:
    app.add_middleware(RequestIDMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    require_schema(engine, "key_manager")


# --- Schemas ---

class SecretIn(BaseModel):
    name: str = Field(..., max_length=128)
    secret_type: str = "symmetric"
    payload: str
    algorithm: str | None = "aes"
    bit_length: int | None = 256
    mode: str | None = "cbc"


class SecretOut(BaseModel):
    id: str
    name: str
    secret_type: str
    payload: str | None = None
    algorithm: str | None = None
    bit_length: int | None = None
    mode: str | None = None
    project_id: str
    created_at: datetime
    updated_at: datetime


class ContainerIn(BaseModel):
    name: str = Field(..., max_length=128)
    container_type: str = "generic"
    secret_refs: list[str] = Field(default_factory=list)


class ContainerOut(BaseModel):
    id: str
    name: str
    container_type: str
    secret_refs: list[str]
    project_id: str
    created_at: datetime
    updated_at: datetime


# --- Routes ---

@app.get("/health")
def health():
    return {"status": "ok", "service": "key-manager-api"}


@app.post("/secrets", response_model=SecretOut, status_code=201)
def create_secret(payload: SecretIn, db: DB, user: dict = Depends(require_auth)):
    project_id = user.get("project_id", "default")
    encrypted_bytes = _cipher.encrypt(payload.payload.encode("utf-8"))
    secret = Secret(
        name=payload.name,
        secret_type=payload.secret_type,
        encrypted_payload=encrypted_bytes.decode("utf-8"),
        algorithm=payload.algorithm,
        bit_length=payload.bit_length,
        mode=payload.mode,
        project_id=project_id,
    )
    db.add(secret)
    db.commit()
    db.refresh(secret)
    return SecretOut(
        id=secret.id,
        name=secret.name,
        secret_type=secret.secret_type,
        payload=None,
        algorithm=secret.algorithm,
        bit_length=secret.bit_length,
        mode=secret.mode,
        project_id=secret.project_id,
        created_at=secret.created_at,
        updated_at=secret.updated_at,
    )


@app.get("/secrets", response_model=list[SecretOut])
def list_secrets(db: DB, user: dict = Depends(require_auth)):
    project_id = user.get("project_id", "default")
    stmt = select(Secret)
    if user.get("role") != "admin":
        stmt = stmt.where(Secret.project_id == project_id)
    secrets = db.scalars(stmt).all()
    return [
        SecretOut(
            id=s.id,
            name=s.name,
            secret_type=s.secret_type,
            payload=None,
            algorithm=s.algorithm,
            bit_length=s.bit_length,
            mode=s.mode,
            project_id=s.project_id,
            created_at=s.created_at,
            updated_at=s.updated_at,
        )
        for s in secrets
    ]


@app.get("/secrets/{secret_id}", response_model=SecretOut)
def get_secret(secret_id: str, db: DB, user: dict = Depends(require_auth)):
    secret = db.get(Secret, secret_id)
    if not secret:
        raise HTTPException(status_code=404, detail="Secret not found")
    if user.get("role") != "admin" and secret.project_id != user.get("project_id"):
        raise HTTPException(status_code=403, detail="Forbidden")

    decrypted_bytes = _cipher.decrypt(secret.encrypted_payload.encode("utf-8"))
    decrypted_payload = decrypted_bytes.decode("utf-8")

    return SecretOut(
        id=secret.id,
        name=secret.name,
        secret_type=secret.secret_type,
        payload=decrypted_payload,
        algorithm=secret.algorithm,
        bit_length=secret.bit_length,
        mode=secret.mode,
        project_id=secret.project_id,
        created_at=secret.created_at,
        updated_at=secret.updated_at,
    )


@app.delete("/secrets/{secret_id}", status_code=204)
def delete_secret(secret_id: str, db: DB, user: dict = Depends(require_auth)):
    secret = db.get(Secret, secret_id)
    if not secret:
        raise HTTPException(status_code=404, detail="Secret not found")
    if user.get("role") != "admin" and secret.project_id != user.get("project_id"):
        raise HTTPException(status_code=403, detail="Forbidden")

    db.delete(secret)
    db.commit()


@app.post("/containers", response_model=ContainerOut, status_code=201)
def create_container(payload: ContainerIn, db: DB, user: dict = Depends(require_auth)):
    project_id = user.get("project_id", "default")
    container = SecretContainer(
        name=payload.name,
        container_type=payload.container_type,
        secret_refs_json=json.dumps(payload.secret_refs),
        project_id=project_id,
    )
    db.add(container)
    db.commit()
    db.refresh(container)
    return ContainerOut(
        id=container.id,
        name=container.name,
        container_type=container.container_type,
        secret_refs=json.loads(container.secret_refs_json),
        project_id=container.project_id,
        created_at=container.created_at,
        updated_at=container.updated_at,
    )


@app.get("/containers", response_model=list[ContainerOut])
def list_containers(db: DB, user: dict = Depends(require_auth)):
    project_id = user.get("project_id", "default")
    stmt = select(SecretContainer)
    if user.get("role") != "admin":
        stmt = stmt.where(SecretContainer.project_id == project_id)
    containers = db.scalars(stmt).all()
    return [
        ContainerOut(
            id=c.id,
            name=c.name,
            container_type=c.container_type,
            secret_refs=json.loads(c.secret_refs_json),
            project_id=c.project_id,
            created_at=c.created_at,
            updated_at=c.updated_at,
        )
        for c in containers
    ]


@app.get("/containers/{container_id}", response_model=ContainerOut)
def get_container(container_id: str, db: DB, user: dict = Depends(require_auth)):
    container = db.get(SecretContainer, container_id)
    if not container:
        raise HTTPException(status_code=404, detail="Container not found")
    if user.get("role") != "admin" and container.project_id != user.get("project_id"):
        raise HTTPException(status_code=403, detail="Forbidden")

    return ContainerOut(
        id=container.id,
        name=container.name,
        container_type=container.container_type,
        secret_refs=json.loads(container.secret_refs_json),
        project_id=container.project_id,
        created_at=container.created_at,
        updated_at=container.updated_at,
    )


@app.delete("/containers/{container_id}", status_code=204)
def delete_container(container_id: str, db: DB, user: dict = Depends(require_auth)):
    container = db.get(SecretContainer, container_id)
    if not container:
        raise HTTPException(status_code=404, detail="Container not found")
    if user.get("role") != "admin" and container.project_id != user.get("project_id"):
        raise HTTPException(status_code=403, detail="Forbidden")

    db.delete(container)
    db.commit()
