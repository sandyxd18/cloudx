import base64
import hashlib

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa
from fastapi import APIRouter, HTTPException
from shared.auth_middleware import CurrentUser
from sqlalchemy import select

from ..db import DB
from ..lookups import get_keypair, require_keypair
from ..models import Keypair
from ..schemas import KeypairImportIn, KeypairIn
from ..serializers import obj_to_dict

router = APIRouter()


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


def generate_keypair_material(key_type: str = "rsa") -> tuple[str, str]:
    """Generate an SSH key pair. Returns (private_key_pem, public_key_openssh)."""
    if key_type == "ed25519":
        private_key = ed25519.Ed25519PrivateKey.generate()
        # Ed25519 has no "TraditionalOpenSSL" PKCS1 form — PEM here is PKCS8, which
        # OpenSSH, PuTTYgen, and every mainstream SSH client accept transparently.
        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("ascii")
    else:
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


@router.post("/keypairs", status_code=201)
def create_keypair(payload: KeypairIn, _: CurrentUser, db: DB):
    if get_keypair(db, payload.name):
        raise HTTPException(status_code=409, detail="keypair already exists")
    key_type = payload.key_type if payload.key_type in ("rsa", "ed25519") else "rsa"
    private_key, public_key = generate_keypair_material(key_type)
    keypair = Keypair(name=payload.name, public_key=public_key, fingerprint=public_key_fingerprint(public_key), key_type=key_type)
    db.add(keypair)
    db.commit()
    db.refresh(keypair)
    data = obj_to_dict(keypair)
    data["private_key"] = private_key
    return data


@router.post("/keypairs/import", status_code=201)
def import_keypair(payload: KeypairImportIn, _: CurrentUser, db: DB):
    if get_keypair(db, payload.name):
        raise HTTPException(status_code=409, detail="keypair already exists")
    public_key = payload.public_key.strip()
    keypair = Keypair(name=payload.name, public_key=public_key, fingerprint=public_key_fingerprint(public_key))
    db.add(keypair)
    db.commit()
    db.refresh(keypair)
    return obj_to_dict(keypair)


@router.get("/keypairs")
def list_keypairs(_: CurrentUser, db: DB):
    return {"items": [obj_to_dict(k) for k in db.scalars(select(Keypair).order_by(Keypair.created_at)).all()]}


@router.get("/keypairs/{name}")
def show_keypair(name: str, _: CurrentUser, db: DB):
    return obj_to_dict(require_keypair(db, name))


@router.delete("/keypairs/{name}")
def delete_keypair(name: str, _: CurrentUser, db: DB):
    keypair = require_keypair(db, name)
    db.delete(keypair)
    db.commit()
    return {"status": "deleted", "id": keypair.id, "name": keypair.name}
