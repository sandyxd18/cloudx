import os

import requests
from fastapi import HTTPException
from shared.internal import internal_headers
from sqlalchemy import func, select

from .config import log

AUTH_API_URL = os.getenv("AUTH_API_URL", "http://auth-api:8000").rstrip("/")


def check_quota(db, project_id: str | None, model, key: str) -> None:
    """413 when the project already owns `max_<key>` resources of this kind (limits live in auth-api).

    ponytail: fails open (logged) when auth-api is unreachable, like compute's quota check.
    """
    if not project_id:
        return
    try:
        r = requests.get(f"{AUTH_API_URL}/internal/projects/{project_id}/quota", headers=internal_headers(), timeout=5)
        r.raise_for_status()
        limit = r.json().get(f"max_{key}")
    except (requests.RequestException, ValueError) as exc:
        log.warning("quota check skipped (auth-api): %s", exc)
        return
    if limit is None:
        return
    used = db.scalar(select(func.count()).select_from(model).where(model.project_id == project_id))
    if used >= limit:
        raise HTTPException(status_code=413, detail=f"Quota exceeded: max_{key} ({limit}) reached")
