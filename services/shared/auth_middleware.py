"""
cloudx shared auth middleware
==============================
Drop this file into each service's app/ directory (via Dockerfile COPY).

Usage in a FastAPI service:

    from app.auth_middleware import require_auth, require_admin, OptionalAuth, CurrentUser, AdminUser

    @app.get("/resource")
    def list_resource(current: CurrentUser, db: DB):
        # current["project_id"] is the authenticated project
        ...

Authentication methods (checked in order):
  1. Authorization: Bearer <jwt>
  2. X-Auth-Token: <jwt>
  3. X-Access-Key-Id + X-Secret-Access-Key  →  calls auth-api to exchange for token
"""

import hmac
import inspect
import logging
import os
import time
from typing import Annotated

import requests
from fastapi import Depends, HTTPException, Request
from jose import JWTError, jwt
log = logging.getLogger("cloudx-auth")

AUTH_API_URL = os.getenv("AUTH_API_URL", "http://auth-api:8000")
SECRET_KEY   = os.environ["AUTH_SECRET_KEY"]
ALGORITHM    = "HS256"

_OPEN_PATHS = {"/health", "/metrics", "/openapi.json", "/docs", "/redoc"}


def _extract_token(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    token = request.headers.get("X-Auth-Token", "").strip()
    if token:
        return token
    return None


def _exchange_access_key(request: Request) -> str | None:
    """Exchange access key headers for a JWT by calling auth-api."""
    key_id = request.headers.get("X-Access-Key-Id", "")
    secret = request.headers.get("X-Secret-Access-Key", "")
    if not key_id or not secret:
        return None
    try:
        resp = requests.post(
            f"{AUTH_API_URL}/auth/tokens/access-key",
            headers={"X-Access-Key-Id": key_id, "X-Secret-Access-Key": secret},
            timeout=5,
        )
        if resp.status_code == 201:
            return resp.json().get("token")
    except Exception as exc:
        log.warning("access-key exchange with auth-api failed: %s", exc)
    return None


_REVOKE_TTL = 30
_ok_jti: dict[str, float] = {}  # jti -> until when "not revoked" is trusted


def _check_revoked(token: str, claims: dict) -> None:
    """Logout only writes to auth-api, so ask it (cached 30s) whether this token was revoked.

    ponytail: fails open if auth-api is unreachable (an auth outage must not take every API down);
    revocation latency is up to _REVOKE_TTL seconds. REVOCATION_CHECK=false skips it.
    """
    jti = claims.get("jti")
    if not jti or os.getenv("REVOCATION_CHECK", "true").lower() == "false":
        return
    now = time.time()
    if _ok_jti.get(jti, 0) > now:
        return
    try:
        r = requests.get(f"{AUTH_API_URL}/auth/tokens/validate", headers={"Authorization": f"Bearer {token}"}, timeout=3)
    except requests.RequestException as exc:
        log.warning("revocation check skipped, auth-api unreachable: %s", exc)
        return
    if r.status_code == 401:
        raise HTTPException(status_code=401, detail="token has been revoked")
    if r.status_code < 400:
        if len(_ok_jti) > 4096:
            _ok_jti.clear()
        _ok_jti[jti] = now + _REVOKE_TTL


def _decode_jwt(token: str) -> dict:
    try:
        claims = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError as e:
        raise HTTPException(status_code=401, detail=f"invalid token: {e}")
    _check_revoked(token, claims)
    return claims


READ_ONLY_METHODS = {"GET", "HEAD", "OPTIONS"}


def check_reader(claims: dict, request: Request) -> dict:
    """`reader` role (OpenStack's read-only role): may call safe methods only. Logout stays allowed."""
    if claims.get("role") == "reader" and request.method not in READ_ONLY_METHODS \
            and not request.url.path.startswith("/auth/tokens"):
        raise HTTPException(status_code=403, detail="reader role is read-only")
    return claims


def require_auth(request: Request) -> dict:
    """FastAPI dependency — returns JWT payload dict."""
    token = _extract_token(request)
    if not token:
        token = _exchange_access_key(request)
    if not token:
        raise HTTPException(status_code=401, detail="authentication required: provide X-Auth-Token or access key headers")
    return check_reader(_decode_jwt(token), request)


def require_admin(current: dict = Depends(require_auth)) -> dict:
    if current.get("role") != "admin":
        raise HTTPException(status_code=403, detail="admin role required")
    return current


def require_internal(request: Request) -> None:
    """Service-to-service routes (/internal/*): `X-Internal-Token: $INTERNAL_TOKEN`, or an admin JWT.

    ponytail: with INTERNAL_TOKEN unset the routes stay open (legacy installs); the installer generates one.
    """
    token = os.getenv("INTERNAL_TOKEN", "")
    if not token or hmac.compare_digest(request.headers.get("X-Internal-Token", ""), token):
        return
    require_admin(require_auth(request))


def require_user_or_internal(request: Request) -> dict:
    """User JWT, or a service holding INTERNAL_TOKEN (acts as admin). Unlike require_internal, never open."""
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
    auth_func = getattr(getattr(request, "app", None), "dependency_overrides", {}).get(require_auth, require_auth)
    if len(inspect.signature(auth_func).parameters) > 0:
        return auth_func(request)
    return auth_func()


def require_admin_or_internal(request: Request) -> dict:
    """Admin JWT, or a service holding INTERNAL_TOKEN. Never open, unlike require_internal."""
    return require_admin(require_user_or_internal(request))


def optional_auth(request: Request) -> dict | None:
    """Returns payload if authenticated, None otherwise. For endpoints that work either way."""
    try:
        return require_auth(request)
    except HTTPException:
        return None


CurrentUser  = Annotated[dict, Depends(require_auth)]
AdminUser    = Annotated[dict, Depends(require_admin)]
OptionalAuth = Annotated[dict | None, Depends(optional_auth)]
UserOrInternal = Annotated[dict, Depends(require_user_or_internal)]
AdminOrInternal = Annotated[dict, Depends(require_admin_or_internal)]
InternalAuth = Depends(require_internal)  # `dependencies=[InternalAuth]` on /internal routes
