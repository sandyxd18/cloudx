import secrets
import string
import uuid
from datetime import timedelta

import bcrypt
from fastapi import HTTPException
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import ALGORITHM, SECRET_KEY, TOKEN_EXPIRE_H
from .models import RoleAssignment, ServiceCatalogEntry, ServiceEndpoint, User, utcnow


# ── Helpers ────────────────────────────────────────────────────────────────────
def _gen_access_key_id() -> str:
    """Generate CLDX-prefixed 20-char access key ID."""
    charset = string.ascii_uppercase + string.digits
    suffix  = "".join(secrets.choice(charset) for _ in range(16))
    return f"CLDX{suffix}"


def _gen_secret_key() -> str:
    """Generate 40-char URL-safe secret access key."""
    return secrets.token_urlsafe(30)


def _hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def _verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except (ValueError, TypeError, AttributeError):
        return False


def project_role(db: Session, user: User, project_id: str) -> str:
    """Role of the user inside a project: explicit assignment, else the user's default role."""
    a = db.get(RoleAssignment, (user.id, project_id))
    return a.role if a else user.role


def _create_token(
    user: User,
    scope_type: str = "project",
    extra_roles: list[str] | None = None,
    project_name: str | None = None,
    role: str | None = None,
) -> str:
    """Create a JWT token.

    Args:
        user: the authenticated user.
        scope_type: 'project' (default) | 'domain' | 'system'.
        extra_roles: additional roles from an Application Credential (overrides user.role).
        project_name: active project name to include in payload.
        role: role in the active project (defaults to user.role).
    """
    jti = str(uuid.uuid4())
    payload = {
        "jti":          jti,
        "sub":          user.id,
        "username":     user.username,
        "project_id":   user.project_id,
        "project_name": project_name or "",
        "role":         (extra_roles[0] if extra_roles else (role or user.role)),
        "roles":        (extra_roles if extra_roles else [role or user.role]),
        "scope_type":   scope_type,
        "domain_id":    user.domain_id or "default",
        "domain_admin": bool(user.is_domain_admin),
        "exp":          utcnow() + timedelta(hours=TOKEN_EXPIRE_H),
        "iat":          utcnow(),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def _build_catalog(db: Session) -> list:
    """Build service catalog from DB."""
    catalog = []
    services = db.scalars(select(ServiceCatalogEntry).where(ServiceCatalogEntry.enabled == True)).all()
    for svc in services:
        endpoints = db.scalars(
            select(ServiceEndpoint).where(
                ServiceEndpoint.service_id == svc.id,
                ServiceEndpoint.enabled == True,
            )
        ).all()
        catalog.append({
            "id":        svc.id,
            "name":      svc.name,
            "type":      svc.type,
            "endpoints": [
                {"id": ep.id, "interface": ep.interface, "region": ep.region, "url": ep.url}
                for ep in endpoints
            ],
        })
    return catalog


def _decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError as e:
        raise HTTPException(status_code=401, detail=f"invalid token: {e}")
