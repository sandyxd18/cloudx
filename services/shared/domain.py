"""Which projects may a caller see? Domain admins cover every project of their domain (mapping lives in auth-api)."""
import os
import time

import requests

from shared.internal import internal_headers

_TTL = 30
_cache: dict[str, tuple[float, list[str]]] = {}


def domain_projects(domain_id: str) -> list[str]:
    """Project ids of a domain, cached 30 s. Fails closed ([]) when auth-api is unreachable."""
    hit = _cache.get(domain_id)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    url = os.getenv("AUTH_API_URL", "http://auth-api:8000").rstrip("/")
    try:
        r = requests.get(f"{url}/internal/domains/{domain_id}/projects", headers=internal_headers(), timeout=5)
        r.raise_for_status()
        ids = list(r.json()["project_ids"])
    except (requests.RequestException, ValueError, KeyError):
        return []
    _cache[domain_id] = (time.monotonic() + _TTL, ids)
    return ids


def visible_projects(user: dict, domain_id: str | None = None) -> list[str] | None:
    """None = every project (system admin); else the project ids the caller may see.

    `domain_id` narrows a system admin to one domain; a domain admin asking for another domain gets nothing.
    """
    if user.get("role") == "admin":
        return domain_projects(domain_id) if domain_id else None
    if user.get("domain_admin"):
        own = user.get("domain_id", "default")
        return domain_projects(own) if domain_id in (None, own) else []
    return [user["project_id"]] if user.get("project_id") and not domain_id else []


def can_see(user: dict, project_id: str | None) -> bool:
    ids = visible_projects(user)
    return ids is None or not project_id or project_id in ids


def owns(user: dict, project_id: str | None) -> bool:
    """Strict: a row without an owner is NOT visible (unlike can_see)."""
    ids = visible_projects(user)
    return ids is None or (project_id is not None and project_id in ids)


def lock_domain(db, domain_id: str | None) -> None:
    """Serialise quota check + create per domain until the transaction ends (Postgres advisory lock; no-op elsewhere).

    ponytail: the lock is released by the first commit, so the create must commit in the same transaction as the check.
    """
    if domain_id and db.get_bind().dialect.name == "postgresql":
        from sqlalchemy import text
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:d))"), {"d": f"quota:{domain_id}"})
