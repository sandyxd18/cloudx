from typing import Annotated

from fastapi import Depends, HTTPException, Request

from .db import DB
from .models import RevokedToken
from .tokens import _decode_token


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
    return payload


def _require_admin(payload: dict = Depends(_require_token)) -> dict:
    if payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="admin role required")
    return payload


CurrentUser = Annotated[dict, Depends(_require_token)]
AdminUser   = Annotated[dict, Depends(_require_admin)]
