from fastapi import APIRouter
from sqlalchemy import select

from ..db import DB
from ..models import AuditLog
from ..schemas import AuditLogIn

router = APIRouter()


@router.post("/audit-logs", status_code=201)
def create_audit_log(payload: AuditLogIn, db: DB):
    log_entry = AuditLog(
        user_id=payload.user_id,
        username=payload.username,
        project_id=payload.project_id,
        action=payload.action,
        resource_type=payload.resource_type,
        resource_id=payload.resource_id,
        details=payload.details,
    )
    db.add(log_entry)
    db.commit()
    db.refresh(log_entry)
    return {
        "id": log_entry.id,
        "user_id": log_entry.user_id,
        "username": log_entry.username,
        "project_id": log_entry.project_id,
        "action": log_entry.action,
        "resource_type": log_entry.resource_type,
        "resource_id": log_entry.resource_id,
        "details": log_entry.details,
        "timestamp": log_entry.timestamp.isoformat() if log_entry.timestamp else None,
    }


@router.get("/audit-logs")
def list_audit_logs(
    db: DB,
    project_id: str | None = None,
    resource_type: str | None = None,
    limit: int = 100,
):
    query = select(AuditLog).order_by(AuditLog.timestamp.desc())
    if project_id:
        query = query.where(AuditLog.project_id == project_id)
    if resource_type:
        query = query.where(AuditLog.resource_type == resource_type)
    query = query.limit(limit)

    rows = db.scalars(query).all()
    return {
        "items": [
            {
                "id": r.id,
                "user_id": r.user_id,
                "username": r.username,
                "project_id": r.project_id,
                "action": r.action,
                "resource_type": r.resource_type,
                "resource_id": r.resource_id,
                "details": r.details,
                "timestamp": r.timestamp.isoformat() if r.timestamp else None,
            }
            for r in rows
        ]
    }
