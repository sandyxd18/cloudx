import hmac
import os
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from shared import policy
from shared.auth_middleware import READ_ONLY_METHODS, check_reader

from .db import DB
from .models import RevokedToken
from .tokens import _decode_token


def _trust_token_may(request: Request) -> bool:
    """A token issued from a trust acts as the trustor on other services, but on auth-api it may only read and
    log out: anything else (users, roles, credentials, tokens, trusts) could outlive or widen the delegation."""
    return request.method in READ_ONLY_METHODS or (request.method == "DELETE" and request.url.path == "/auth/tokens/current")


def _require_token(request: Request, db: DB) -> dict:
    """Dependency: extract + validate bearer token from Authorization header."""
    auth = request.headers.get("Authorization", "") or request.headers.get("X-Auth-Token", "")
    token = auth.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="authentication required")
    payload = _decode_token(token)
    jti = payload.get("jti", "")
    revoked = db.get(RevokedToken, jti)
    if revoked:
        raise HTTPException(status_code=401, detail="token has been revoked")
    check_reader(payload, request)
    if payload.get("trust_id") and not _trust_token_may(request):
        raise HTTPException(status_code=403, detail="trust tokens cannot modify identity")
    return payload


def _require_admin(payload: dict = Depends(_require_token)) -> dict:
    if payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="admin role required")
    return payload


def _require_user_or_internal(request: Request, user: dict = Depends(_require_token)) -> dict:
    token = os.getenv("INTERNAL_TOKEN", "")
    if token and hmac.compare_digest(request.headers.get("X-Internal-Token", ""), token):
        return {
            "sub": "internal",
            "username": "admin",
            "project_id": "admin",
            "project_name": "admin",
            "role": "admin",
            "roles": ["admin"],
            "scope_type": "system",
            "jti": "internal",
        }
    return user


def _require_domain_admin(payload: dict = Depends(_require_token)) -> dict:
    if payload.get("role") != "admin" and not payload.get("domain_admin"):
        raise HTTPException(status_code=403, detail="admin or domain admin required")
    return payload

def scope_domain(current: dict) -> str | None:
    """None = system admin (every domain); otherwise the one domain this caller may touch."""
    return None if current.get("role") == "admin" else current.get("domain_id", "default")

def in_scope(current: dict, domain_id: str) -> bool:
    return scope_domain(current) in (None, domain_id)

def gate(action: str):
    """Route dependency: the central policy must allow `action`. The token is checked by _require_token
    (revocation, reader rule, trust confinement) and, being cached per request, only once."""
    return Depends(policy.require(action, _require_token))


CurrentUser = Annotated[dict, Depends(_require_token)]
AdminUser   = Annotated[dict, Depends(_require_admin)]
DomainAdminUser = Annotated[dict, Depends(_require_domain_admin)]
UserOrInternal = Annotated[dict, Depends(_require_user_or_internal)]
