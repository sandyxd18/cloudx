from fastapi import APIRouter, HTTPException
from shared.auth_middleware import AdminUser, CurrentUser
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import DB
from ..models import Aggregate, AggregateHost, now
from ..schemas import AggregateIn, AggregateUpdateIn

router = APIRouter()


def _aggregate_to_dict(db: Session, agg: Aggregate) -> dict:
    hosts = [
        row.host for row in db.scalars(
            select(AggregateHost).where(AggregateHost.aggregate_id == agg.id)
        ).all()
    ]
    meta = {}
    try:
        import json as _json
        meta = _json.loads(agg.extra_metadata or "{}")
    except ValueError:
        pass
    return {
        "id":                agg.id,
        "name":              agg.name,
        "availability_zone": agg.availability_zone,
        "hosts":             hosts,
        "metadata":          meta,
        "created_at":        agg.created_at.isoformat() if agg.created_at else None,
        "updated_at":        agg.updated_at.isoformat() if agg.updated_at else None,
    }


@router.post("/os-aggregates", status_code=201)
def create_aggregate(payload: AggregateIn, _: AdminUser, db: DB):
    """Create a host aggregate (admin only)."""
    if db.scalar(select(Aggregate).where(Aggregate.name == payload.name)):
        raise HTTPException(status_code=409, detail="aggregate already exists")
    agg = Aggregate(name=payload.name, availability_zone=payload.availability_zone)
    db.add(agg)
    db.commit()
    db.refresh(agg)
    return {"aggregate": _aggregate_to_dict(db, agg)}


@router.get("/os-aggregates")
def list_aggregates(_: CurrentUser, db: DB):
    """List all host aggregates."""
    aggs = db.scalars(select(Aggregate).order_by(Aggregate.created_at)).all()
    return {"aggregates": [_aggregate_to_dict(db, a) for a in aggs]}


@router.get("/os-aggregates/{agg_ref}")
def show_aggregate(agg_ref: str, _: CurrentUser, db: DB):
    """Get details of a specific host aggregate."""
    agg = db.get(Aggregate, agg_ref) or db.scalar(select(Aggregate).where(Aggregate.name == agg_ref))
    if not agg:
        raise HTTPException(status_code=404, detail="aggregate not found")
    return {"aggregate": _aggregate_to_dict(db, agg)}


@router.put("/os-aggregates/{agg_ref}")
def update_aggregate(agg_ref: str, payload: AggregateUpdateIn, _: AdminUser, db: DB):
    """Update aggregate name or availability zone (admin only)."""
    agg = db.get(Aggregate, agg_ref) or db.scalar(select(Aggregate).where(Aggregate.name == agg_ref))
    if not agg:
        raise HTTPException(status_code=404, detail="aggregate not found")
    if payload.name is not None:
        agg.name = payload.name
    if payload.availability_zone is not None:
        agg.availability_zone = payload.availability_zone
    agg.updated_at = now()
    db.commit()
    return {"aggregate": _aggregate_to_dict(db, agg)}


@router.delete("/os-aggregates/{agg_ref}", status_code=204)
def delete_aggregate(agg_ref: str, _: AdminUser, db: DB):
    """Delete a host aggregate (admin only)."""
    agg = db.get(Aggregate, agg_ref) or db.scalar(select(Aggregate).where(Aggregate.name == agg_ref))
    if not agg:
        raise HTTPException(status_code=404, detail="aggregate not found")
    # Cascade delete removes AggregateHost rows automatically
    db.delete(agg)
    db.commit()


@router.post("/os-aggregates/{agg_ref}/action")
def aggregate_action(agg_ref: str, payload: dict, _: AdminUser, db: DB):
    """
    Perform an action on an aggregate.
    Supported actions:
      - add_host:    {"add_host": {"host": "hostname"}}
      - remove_host: {"remove_host": {"host": "hostname"}}
    """
    agg = db.get(Aggregate, agg_ref) or db.scalar(select(Aggregate).where(Aggregate.name == agg_ref))
    if not agg:
        raise HTTPException(status_code=404, detail="aggregate not found")

    if "add_host" in payload:
        host = payload["add_host"].get("host")
        if not host:
            raise HTTPException(status_code=400, detail="host is required")
        # Check not already a member
        existing = db.scalar(
            select(AggregateHost).where(
                AggregateHost.aggregate_id == agg.id,
                AggregateHost.host == host,
            )
        )
        if existing:
            raise HTTPException(status_code=409, detail=f"host '{host}' is already in this aggregate")
        db.add(AggregateHost(aggregate_id=agg.id, host=host))
        agg.updated_at = now()
        db.commit()
        return {"aggregate": _aggregate_to_dict(db, agg)}

    elif "remove_host" in payload:
        host = payload["remove_host"].get("host")
        if not host:
            raise HTTPException(status_code=400, detail="host is required")
        record = db.scalar(
            select(AggregateHost).where(
                AggregateHost.aggregate_id == agg.id,
                AggregateHost.host == host,
            )
        )
        if not record:
            raise HTTPException(status_code=404, detail=f"host '{host}' not found in aggregate")
        db.delete(record)
        agg.updated_at = now()
        db.commit()
        return {"aggregate": _aggregate_to_dict(db, agg)}

    else:
        raise HTTPException(status_code=400, detail=f"unknown action: {list(payload.keys())}")
