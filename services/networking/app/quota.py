import os

import requests
from fastapi import HTTPException
from shared.domain import lock_domain
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
        body = r.json()
        limit = body.get(f"max_{key}")
        dom = body.get("domain") or {}
    except (requests.RequestException, ValueError) as exc:
        log.warning("quota check skipped (auth-api): %s", exc)
        return
    cap = (dom.get("caps") or {}).get(f"max_{key}")
    if cap is not None:
        lock_domain(db, dom.get("domain_id"))
    if cap is not None and db.scalar(select(func.count()).select_from(model).where(model.project_id.in_(dom["project_ids"]))) >= cap:
        raise HTTPException(status_code=413, detail=f"Domain quota exceeded: max_{key} ({cap}) reached")
    if limit is None:
        return
    used = db.scalar(select(func.count()).select_from(model).where(model.project_id == project_id))
    if used >= limit:
        raise HTTPException(status_code=413, detail=f"Quota exceeded: max_{key} ({limit}) reached")
