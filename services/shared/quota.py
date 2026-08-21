"""
cloudx centralized quota engine (oslo.quota-inspired)
=======================================================

Provides atomic reserve → commit/rollback quota management,
inspired by oslo.quota. Designed to work with the CloudX
PostgreSQL database via SQLAlchemy.

Usage:
    from shared.quota import QuotaEngine, QuotaError

    quota = QuotaEngine()

    # Reserve resources before creating them
    try:
        res_id = quota.reserve(db, project_id="proj-1", deltas={"instances": 1, "cores": 4})
    except QuotaError as e:
        raise HTTPException(409, str(e))

    try:
        # ... create the resource ...
        quota.commit(db, res_id)
    except Exception:
        quota.rollback(db, res_id)
        raise

Resource Definitions (hard limits per resource type):
    instances       — max number of compute instances
    cores           — max number of vCPUs
    ram             — max RAM in MB
    volumes         — max number of volumes
    gigabytes       — max total storage in GB
    snapshots       — max number of snapshots
    networks        — max number of networks
    routers         — max number of routers
    ports           — max number of ports
    security_groups — max number of security groups
    floating_ips    — max number of floating IPs
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import Integer, String, DateTime, JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

if TYPE_CHECKING:
    pass


# ── Default Limits ─────────────────────────────────────────────────────────────
# These are defaults; project-specific limits should override them if defined
# in the project quota tables (e.g. block_store_volume_quotas).

DEFAULT_LIMITS: dict[str, int] = {
    "instances":       20,
    "cores":           40,
    "ram":             102400,   # MB → 100 GB
    "volumes":         10,
    "gigabytes":       1000,
    "snapshots":       10,
    "networks":        10,
    "subnets":         20,
    "routers":         10,
    "ports":           50,
    "security_groups": 10,
    "floating_ips":    10,
}


# ── ORM Model ──────────────────────────────────────────────────────────────────

class QuotaReservation(DeclarativeBase.__class__):
    """Placeholder; actual model defined per-service using their Base.
    See _get_or_create_model() below for the real implementation pattern.
    """
    pass


_QUOTA_RESERVATION_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS quota_reservations (
    id           VARCHAR PRIMARY KEY,
    project_id   VARCHAR NOT NULL,
    resource     VARCHAR NOT NULL,
    delta        INTEGER NOT NULL,
    status       VARCHAR NOT NULL DEFAULT 'reserved',
    created_at   TIMESTAMP WITH TIME ZONE NOT NULL,
    expires_at   TIMESTAMP WITH TIME ZONE
);
CREATE INDEX IF NOT EXISTS ix_quota_res_project ON quota_reservations(project_id, status);
"""


# ── Exceptions ─────────────────────────────────────────────────────────────────

class QuotaError(Exception):
    """Raised when a quota limit would be exceeded."""
    def __init__(self, resource: str, limit: int, used: int, delta: int):
        self.resource = resource
        self.limit    = limit
        self.used     = used
        self.delta    = delta
        super().__init__(
            f"Quota exceeded for resource '{resource}': "
            f"limit={limit}, current={used}, requested={delta}"
        )


# ── Low-level DB helpers (raw SQL for portability across services) ─────────────

def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _get_reserved(db: Session, project_id: str, resource: str) -> int:
    """Sum of pending reservations (status='reserved') for a resource."""
    result = db.execute(
        __import__("sqlalchemy").text(
            "SELECT COALESCE(SUM(delta), 0) FROM quota_reservations "
            "WHERE project_id = :pid AND resource = :res AND status = 'reserved'"
        ),
        {"pid": project_id, "res": resource},
    ).scalar()
    return int(result or 0)


def _get_reservation(db: Session, reservation_id: str) -> dict | None:
    row = db.execute(
        __import__("sqlalchemy").text(
            "SELECT id, project_id, resource, delta, status "
            "FROM quota_reservations WHERE id = :rid"
        ),
        {"rid": reservation_id},
    ).fetchone()
    if row is None:
        return None
    return dict(row._mapping)


def _create_reservation(db: Session, project_id: str, resource: str, delta: int) -> str:
    rid = str(uuid.uuid4())
    db.execute(
        __import__("sqlalchemy").text(
            "INSERT INTO quota_reservations (id, project_id, resource, delta, status, created_at) "
            "VALUES (:id, :pid, :res, :delta, 'reserved', :now)"
        ),
        {"id": rid, "pid": project_id, "res": resource, "delta": delta, "now": _utcnow()},
    )
    return rid


def _update_reservation_status(db: Session, reservation_id: str, status: str) -> None:
    db.execute(
        __import__("sqlalchemy").text(
            "UPDATE quota_reservations SET status = :status WHERE id = :rid"
        ),
        {"status": status, "rid": reservation_id},
    )


def ensure_quota_table(db: Session) -> None:
    """Idempotently create the quota_reservations table if it does not exist."""
    for stmt in _QUOTA_RESERVATION_TABLE_DDL.strip().split(";\n"):
        stmt = stmt.strip()
        if stmt:
            db.execute(__import__("sqlalchemy").text(stmt + ";"))
    db.commit()


# ── QuotaEngine ────────────────────────────────────────────────────────────────

class QuotaEngine:
    """
    Atomic quota management engine.

    Workflow:
        reservation_ids = engine.reserve(db, project_id, deltas, current_usage_fn)
        ...create resources...
        engine.commit(db, reservation_ids)   # on success
        # or
        engine.rollback(db, reservation_ids) # on failure
    """

    def __init__(self, limits: dict[str, int] | None = None):
        """
        Args:
            limits: Optional override for DEFAULT_LIMITS. Used for project-specific limits.
        """
        self._limits = {**DEFAULT_LIMITS, **(limits or {})}

    def reserve(
        self,
        db: Session,
        project_id: str,
        deltas: dict[str, int],
        current_usage: dict[str, int] | None = None,
        project_limits: dict[str, int] | None = None,
    ) -> list[str]:
        """
        Reserve resources. Raises QuotaError if any limit would be exceeded.

        Args:
            db:              SQLAlchemy session.
            project_id:      Project to check quotas for.
            deltas:          Dict of {resource: amount_to_reserve}.
            current_usage:   Dict of {resource: current_used}. If not provided,
                             only reserved-but-uncommitted amounts are counted.
            project_limits:  Per-project overrides for DEFAULT_LIMITS.

        Returns:
            List of reservation IDs (one per resource in deltas).
        """
        limits = {**self._limits, **(project_limits or {})}
        reservation_ids: list[str] = []

        for resource, delta in deltas.items():
            if delta <= 0:
                continue

            limit = limits.get(resource, DEFAULT_LIMITS.get(resource, 0))
            # Already committed usage (passed in from the service's own tables)
            used = (current_usage or {}).get(resource, 0)
            # In-flight reservations not yet committed
            in_flight = _get_reserved(db, project_id, resource)

            total_would_be = used + in_flight + delta

            if limit > 0 and total_would_be > limit:
                # Rollback any reservations already made in this batch
                for rid in reservation_ids:
                    _update_reservation_status(db, rid, "rolled_back")
                db.commit()
                raise QuotaError(resource, limit, used + in_flight, delta)

            rid = _create_reservation(db, project_id, resource, delta)
            reservation_ids.append(rid)

        db.commit()
        return reservation_ids

    def commit(self, db: Session, reservation_ids: list[str]) -> None:
        """Mark reservations as committed (resources have been created)."""
        for rid in reservation_ids:
            _update_reservation_status(db, rid, "committed")
        db.commit()

    def rollback(self, db: Session, reservation_ids: list[str]) -> None:
        """Mark reservations as rolled_back (resource creation failed)."""
        for rid in reservation_ids:
            _update_reservation_status(db, rid, "rolled_back")
        db.commit()

    def get_usage(self, db: Session, project_id: str) -> dict[str, dict]:
        """
        Return current reserved (in-flight) amounts per resource for a project.
        Actual usage must be queried from the service's own tables separately.
        """
        result = db.execute(
            __import__("sqlalchemy").text(
                "SELECT resource, COALESCE(SUM(delta), 0) as reserved "
                "FROM quota_reservations "
                "WHERE project_id = :pid AND status = 'reserved' "
                "GROUP BY resource"
            ),
            {"pid": project_id},
        ).fetchall()
        reserved = {row.resource: int(row.reserved) for row in result}
        return {
            res: {"limit": self._limits.get(res, 0), "reserved": reserved.get(res, 0)}
            for res in self._limits
        }


# ── Module-level singleton ─────────────────────────────────────────────────────
# Services can import and use the default engine directly.

default_quota = QuotaEngine()
