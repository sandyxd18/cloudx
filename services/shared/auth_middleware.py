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

import logging
import os
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


def _decode_jwt(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError as e:
        raise HTTPException(status_code=401, detail=f"invalid token: {e}")


def require_auth(request: Request) -> dict:
    """FastAPI dependency — returns JWT payload dict."""
    token = _extract_token(request)
    if not token:
        token = _exchange_access_key(request)
    if not token:
        raise HTTPException(status_code=401, detail="authentication required: provide X-Auth-Token or access key headers")
    return _decode_jwt(token)


def require_admin(current: dict = Depends(require_auth)) -> dict:
    if current.get("role") != "admin":
        raise HTTPException(status_code=403, detail="admin role required")
    return current


def optional_auth(request: Request) -> dict | None:
    """Returns payload if authenticated, None otherwise. For endpoints that work either way."""
    try:
        return require_auth(request)
    except HTTPException:
        return None


CurrentUser  = Annotated[dict, Depends(require_auth)]
AdminUser    = Annotated[dict, Depends(require_admin)]
OptionalAuth = Annotated[dict | None, Depends(optional_auth)]
