from fastapi import HTTPException
from sqlalchemy import or_


def is_admin(current: dict) -> bool:
    return current.get("role") == "admin"


def can_see(current: dict, obj) -> bool:
    # project_id NULL = created before ownership existed or a global default; visible to everyone
    pid = getattr(obj, "project_id", None)
    if is_admin(current) or pid is None or pid == current.get("project_id"):
        return True
    return bool(getattr(obj, "shared", False) or getattr(obj, "external", False))


def own(current: dict, obj, label: str):
    """Return obj, or 404 (not 403, so other projects' ids are not revealed)."""
    if not can_see(current, obj):
        raise HTTPException(status_code=404, detail=f"{label} not found")
    return obj


def scope(q, model, current: dict):
    """Restrict a select() to what the caller may see."""
    if is_admin(current):
        return q
    cond = [model.project_id.is_(None), model.project_id == current.get("project_id")]
    for flag in ("shared", "external"):
        if hasattr(model, flag):
            cond.append(getattr(model, flag).is_(True))
    return q.where(or_(*cond))


def require_owned(current: dict, db, model, ref: str, label: str):
    from .serializers import require_by_ref

    return own(current, require_by_ref(db, model, ref, label), label)
