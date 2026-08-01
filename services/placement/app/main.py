# Placement API — OpenStack-like resource placement and tracking for cloudx.
#
# Tracks:
#   • ResourceProviders   — compute nodes (single-node lab: one provider)
#   • Inventories         — VCPU, MEMORY_MB, DISK_GB capacity per provider
#   • Allocations         — resource claims per consumer (server)
#   • Traits              — capabilities (HW_CPU_X86_AVX2, etc.)
#
# compute-api calls:
#   POST /allocations        when creating a server
#   DELETE /allocations/{id} when deleting a server
#   GET  /usages             for project-level resource usage

import os
import uuid
from datetime import datetime, timezone
from typing import Annotated

from app.auth_middleware import CurrentUser
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import (
    Float, Integer, JSON, String, ForeignKey, DateTime,
    create_engine, select, UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://mini:mini@postgres:5432/mini_openstack")

engine       = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def new_uuid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ── Models ─────────────────────────────────────────────────────────────────────
class ResourceProvider(Base):
    __tablename__ = "placement_resource_providers"
    uuid:               Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    name:               Mapped[str] = mapped_column(String, unique=True)
    parent_provider_id: Mapped[str | None] = mapped_column(String, nullable=True)
    generation:         Mapped[int] = mapped_column(Integer, default=0)
    created_at:         Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Inventory(Base):
    __tablename__ = "placement_inventories"
    id:                  Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    resource_provider_id:Mapped[str] = mapped_column(String, ForeignKey("placement_resource_providers.uuid"))
    resource_class:      Mapped[str] = mapped_column(String)   # VCPU | MEMORY_MB | DISK_GB
    total:               Mapped[int] = mapped_column(Integer)
    reserved:            Mapped[int] = mapped_column(Integer, default=0)
    min_unit:            Mapped[int] = mapped_column(Integer, default=1)
    max_unit:            Mapped[int] = mapped_column(Integer, default=1)
    step_size:           Mapped[int] = mapped_column(Integer, default=1)
    allocation_ratio:    Mapped[float] = mapped_column(Float, default=1.0)
    __table_args__ = (UniqueConstraint("resource_provider_id", "resource_class"),)


class Allocation(Base):
    __tablename__ = "placement_allocations"
    id:                  Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    consumer_uuid:       Mapped[str] = mapped_column(String, index=True)   # server id
    project_id:          Mapped[str] = mapped_column(String, index=True)
    resource_provider_id:Mapped[str] = mapped_column(String, ForeignKey("placement_resource_providers.uuid"))
    resource_class:      Mapped[str] = mapped_column(String)
    used:                Mapped[int] = mapped_column(Integer)
    created_at:          Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Trait(Base):
    __tablename__ = "placement_traits"
    name: Mapped[str] = mapped_column(String, primary_key=True)


class ResourceProviderTrait(Base):
    __tablename__ = "placement_rp_traits"
    resource_provider_id: Mapped[str] = mapped_column(String, ForeignKey("placement_resource_providers.uuid"), primary_key=True)
    trait_name:           Mapped[str] = mapped_column(String, ForeignKey("placement_traits.name"), primary_key=True)


# ── DB dep ─────────────────────────────────────────────────────────────────────
def _get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

DB = Annotated[Session, Depends(_get_db)]


# ── Schemas ────────────────────────────────────────────────────────────────────
class RPIn(BaseModel):
    name: str
    parent_provider_id: str | None = None


class InventoryItem(BaseModel):
    total: int
    reserved: int = 0
    min_unit: int = 1
    max_unit: int = 1
    step_size: int = 1
    allocation_ratio: float = 1.0


class InventoriesIn(BaseModel):
    inventories: dict[str, InventoryItem]   # resource_class → item


class AllocationResource(BaseModel):
    resources: dict[str, int]   # resource_class → amount


class AllocationIn(BaseModel):
    consumer_uuid: str
    project_id: str
    allocations: dict[str, AllocationResource]   # rp_uuid → resources


class TraitIn(BaseModel):
    traits: list[str]


# ── Helpers ────────────────────────────────────────────────────────────────────
def _rp_to_dict(rp: ResourceProvider) -> dict:
    return {
        "uuid": rp.uuid, "name": rp.name,
        "parent_provider_id": rp.parent_provider_id,
        "generation": rp.generation,
        "created_at": rp.created_at.isoformat(),
    }


def _get_rp(db: Session, uuid_or_name: str) -> ResourceProvider:
    rp = db.get(ResourceProvider, uuid_or_name)
    if not rp:
        rp = db.scalar(select(ResourceProvider).where(ResourceProvider.name == uuid_or_name))
    if not rp:
        raise HTTPException(status_code=404, detail="resource provider not found")
    return rp


def _get_or_create_default_rp(db: Session) -> ResourceProvider:
    """Return the single-node compute provider, creating if absent."""
    rp = db.scalar(select(ResourceProvider).where(ResourceProvider.name == "cloudx-compute-0"))
    if not rp:
        import socket
        hostname = socket.gethostname()
        rp = ResourceProvider(name="cloudx-compute-0")
        db.add(rp)
        db.commit()
        db.refresh(rp)
    return rp


# ── Bootstrap ──────────────────────────────────────────────────────────────────
def _bootstrap():
    Base.metadata.create_all(bind=engine)
    # Seed standard traits
    standard_traits = [
        "HW_CPU_X86_AVX", "HW_CPU_X86_AVX2", "HW_CPU_X86_SSE4_2",
        "COMPUTE_NET_ATTACH_VIF_VIRTIO_FORWARDER", "COMPUTE_STORAGE_VIRTIO",
        "STORAGE_DISK_SSD", "STORAGE_DISK_HDD",
    ]
    db = SessionLocal()
    try:
        for t in standard_traits:
            if not db.get(Trait, t):
                db.add(Trait(name=t))
        db.commit()
    finally:
        db.close()


# ── App ────────────────────────────────────────────────────────────────────────
app = FastAPI(title="cloudx placement-api", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    _bootstrap()


@app.get("/health")
def health():
    return {"status": "ok", "service": "placement-api"}


# ── Resource Providers ─────────────────────────────────────────────────────────
@app.post("/resource_providers", status_code=201)
def create_rp(payload: RPIn, _: CurrentUser, db: DB):
    if db.scalar(select(ResourceProvider).where(ResourceProvider.name == payload.name)):
        raise HTTPException(status_code=409, detail="resource provider already exists")
    rp = ResourceProvider(**payload.model_dump())
    db.add(rp)
    db.commit()
    db.refresh(rp)
    return _rp_to_dict(rp)


@app.get("/resource_providers")
def list_rps(_: CurrentUser, db: DB):
    rps = db.scalars(select(ResourceProvider)).all()
    return {"resource_providers": [_rp_to_dict(r) for r in rps]}


@app.get("/resource_providers/{rp_uuid}")
def show_rp(rp_uuid: str, _: CurrentUser, db: DB):
    return _rp_to_dict(_get_rp(db, rp_uuid))


@app.delete("/resource_providers/{rp_uuid}", status_code=204)
def delete_rp(rp_uuid: str, _: CurrentUser, db: DB):
    rp = _get_rp(db, rp_uuid)
    db.delete(rp)
    db.commit()


# ── Inventories ────────────────────────────────────────────────────────────────
@app.put("/resource_providers/{rp_uuid}/inventories")
def set_inventories(rp_uuid: str, payload: InventoriesIn, _: CurrentUser, db: DB):
    rp = _get_rp(db, rp_uuid)
    # Delete existing inventories
    existing = db.scalars(select(Inventory).where(Inventory.resource_provider_id == rp.uuid)).all()
    for inv in existing:
        db.delete(inv)
    # Create new inventories
    for rc, item in payload.inventories.items():
        db.add(Inventory(
            resource_provider_id=rp.uuid,
            resource_class=rc,
            **item.model_dump(),
        ))
    rp.generation += 1
    db.commit()
    return {"inventories": payload.inventories}


@app.get("/resource_providers/{rp_uuid}/inventories")
def get_inventories(rp_uuid: str, _: CurrentUser, db: DB):
    rp = _get_rp(db, rp_uuid)
    inventories = db.scalars(select(Inventory).where(Inventory.resource_provider_id == rp.uuid)).all()
    result = {}
    for inv in inventories:
        result[inv.resource_class] = {
            "total": inv.total, "reserved": inv.reserved,
            "min_unit": inv.min_unit, "max_unit": inv.max_unit,
            "step_size": inv.step_size, "allocation_ratio": inv.allocation_ratio,
            "used": _get_used(db, rp.uuid, inv.resource_class),
        }
    return {"inventories": result, "resource_provider_generation": rp.generation}


def _get_used(db: Session, rp_uuid: str, rc: str) -> int:
    allocations = db.scalars(
        select(Allocation).where(
            Allocation.resource_provider_id == rp_uuid,
            Allocation.resource_class == rc,
        )
    ).all()
    return sum(a.used for a in allocations)


# ── Traits ────────────────────────────────────────────────────────────────────
@app.get("/resource_providers/{rp_uuid}/traits")
def get_traits(rp_uuid: str, _: CurrentUser, db: DB):
    rp = _get_rp(db, rp_uuid)
    rp_traits = db.scalars(
        select(ResourceProviderTrait).where(ResourceProviderTrait.resource_provider_id == rp.uuid)
    ).all()
    return {"traits": [t.trait_name for t in rp_traits], "resource_provider_generation": rp.generation}


@app.put("/resource_providers/{rp_uuid}/traits")
def set_traits(rp_uuid: str, payload: TraitIn, _: CurrentUser, db: DB):
    rp = _get_rp(db, rp_uuid)
    # Clear existing
    existing = db.scalars(
        select(ResourceProviderTrait).where(ResourceProviderTrait.resource_provider_id == rp.uuid)
    ).all()
    for t in existing:
        db.delete(t)
    # Add new
    for trait_name in payload.traits:
        if not db.get(Trait, trait_name):
            db.add(Trait(name=trait_name))
        db.add(ResourceProviderTrait(resource_provider_id=rp.uuid, trait_name=trait_name))
    rp.generation += 1
    db.commit()
    return {"traits": payload.traits}


@app.get("/traits")
def list_traits(_: CurrentUser, db: DB):
    traits = db.scalars(select(Trait)).all()
    return {"traits": [t.name for t in traits]}


# ── Allocations ───────────────────────────────────────────────────────────────
@app.post("/allocations", status_code=201)
def create_allocations(payload: AllocationIn, _: CurrentUser, db: DB):
    """Claim resources for a consumer (server)."""
    # Delete any existing allocations for this consumer
    existing = db.scalars(
        select(Allocation).where(Allocation.consumer_uuid == payload.consumer_uuid)
    ).all()
    for a in existing:
        db.delete(a)

    for rp_uuid, resources in payload.allocations.items():
        rp = _get_rp(db, rp_uuid)
        for rc, amount in resources.resources.items():
            # Check inventory
            inv = db.scalar(
                select(Inventory).where(
                    Inventory.resource_provider_id == rp.uuid,
                    Inventory.resource_class == rc,
                )
            )
            if inv:
                used = _get_used(db, rp.uuid, rc)
                available = int((inv.total - inv.reserved) * inv.allocation_ratio) - used
                if amount > available:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Insufficient {rc}: requested {amount}, available {available}"
                    )
            db.add(Allocation(
                consumer_uuid=payload.consumer_uuid,
                project_id=payload.project_id,
                resource_provider_id=rp.uuid,
                resource_class=rc,
                used=amount,
            ))
    db.commit()
    return {"consumer_uuid": payload.consumer_uuid, "status": "allocated"}


@app.get("/allocations/{consumer_uuid}")
def get_allocations(consumer_uuid: str, _: CurrentUser, db: DB):
    allocations = db.scalars(
        select(Allocation).where(Allocation.consumer_uuid == consumer_uuid)
    ).all()
    result = {}
    for a in allocations:
        if a.resource_provider_id not in result:
            result[a.resource_provider_id] = {"resources": {}}
        result[a.resource_provider_id]["resources"][a.resource_class] = a.used
    return {"allocations": result}


@app.delete("/allocations/{consumer_uuid}", status_code=204)
def delete_allocations(consumer_uuid: str, _: CurrentUser, db: DB):
    """Release all resources for a consumer (server deleted)."""
    allocations = db.scalars(
        select(Allocation).where(Allocation.consumer_uuid == consumer_uuid)
    ).all()
    for a in allocations:
        db.delete(a)
    db.commit()


# ── Usages ────────────────────────────────────────────────────────────────────
@app.get("/usages")
def get_usages(current: CurrentUser, db: DB, project_id: str | None = None):
    """Get resource usage, optionally filtered by project."""
    q = select(Allocation)
    if project_id:
        q = q.where(Allocation.project_id == project_id)
    elif current.get("role") != "admin":
        q = q.where(Allocation.project_id == current["project_id"])

    allocations = db.scalars(q).all()
    usages: dict[str, int] = {}
    for a in allocations:
        usages[a.resource_class] = usages.get(a.resource_class, 0) + a.used

    return {"usages": usages, "project_id": project_id or current["project_id"]}


@app.get(\"/resource_providers/{rp_uuid}/usages\")
def get_rp_usages(rp_uuid: str, _: CurrentUser, db: DB):
    rp = _get_rp(db, rp_uuid)
    inventories = db.scalars(select(Inventory).where(Inventory.resource_provider_id == rp.uuid)).all()
    result = {}
    for inv in inventories:
        used = _get_used(db, rp.uuid, inv.resource_class)
        result[inv.resource_class] = {
            "total": inv.total,
            "reserved": inv.reserved,
            "used": used,
            "available": int((inv.total - inv.reserved) * inv.allocation_ratio) - used,
        }
    return {"usages": result, "resource_provider_uuid": rp.uuid}


# ── Microversions ──────────────────────────────────────────────────────────────
# OpenStack Placement uses the "OpenStack-API-Version: placement X.Y" header.
# We support a version range of 1.0 – 1.36.

PLACEMENT_MIN_VERSION = "1.0"
PLACEMENT_MAX_VERSION = "1.36"
PLACEMENT_CURRENT_VERSION = "1.36"


@app.middleware("http")
async def microversion_middleware(request, call_next):
    """Respond with OpenStack-Placement-Microversion headers on every response."""
    response = await call_next(request)
    response.headers["OpenStack-API-Version"] = f"placement {PLACEMENT_CURRENT_VERSION}"
    response.headers["X-OpenStack-Nova-API-Version"] = PLACEMENT_CURRENT_VERSION
    response.headers["Vary"] = "OpenStack-API-Version"
    return response


@app.get("/")
def placement_root(_: CurrentUser):
    """Placement API root — version discovery."""
    return {
        "versions": [{
            "id": "v1.0",
            "max_version": PLACEMENT_MAX_VERSION,
            "min_version": PLACEMENT_MIN_VERSION,
            "status": "CURRENT",
            "links": [{"href": "/", "rel": "self"}],
        }]
    }


# ── Allocation Candidates ──────────────────────────────────────────────────────

@app.get("/allocation_candidates")
def get_allocation_candidates(
    resources: str,
    _: CurrentUser,
    db: DB,
    limit: int = 1000,
):
    """
    Find resource providers that can satisfy the given resource request.

    resources query parameter format: RESOURCE_CLASS:amount[,RESOURCE_CLASS:amount]
    Example: VCPU:2,MEMORY_MB:4096,DISK_GB:50

    Returns a list of candidate provider summaries and allocation requests.
    """
    # Parse resources string
    requested: dict[str, int] = {}
    try:
        for part in resources.split(","):
            rc, amount = part.strip().split(":")
            requested[rc.strip()] = int(amount.strip())
    except Exception:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Invalid resources format. Use RESOURCE_CLASS:amount[,...]")

    rps = db.scalars(select(ResourceProvider)).all()
    candidates = []

    for rp in rps:
        inventories = db.scalars(
            select(Inventory).where(Inventory.resource_provider_id == rp.uuid)
        ).all()
        inv_map = {inv.resource_class: inv for inv in inventories}

        # Check if this RP can satisfy ALL requested resources
        can_satisfy = True
        allocation_request: dict[str, int] = {}

        for rc, amount in requested.items():
            inv = inv_map.get(rc)
            if inv is None:
                can_satisfy = False
                break
            used = _get_used(db, rp.uuid, rc)
            available = int((inv.total - inv.reserved) * inv.allocation_ratio) - used
            if amount > available:
                can_satisfy = False
                break
            allocation_request[rc] = amount

        if can_satisfy:
            # Build provider summary
            summary = {
                "uuid": rp.uuid,
                "name": rp.name,
                "generation": rp.generation,
                "resources": {},
            }
            for rc, inv in inv_map.items():
                used = _get_used(db, rp.uuid, rc)
                summary["resources"][rc] = {
                    "total": inv.total,
                    "reserved": inv.reserved,
                    "used": used,
                    "available": int((inv.total - inv.reserved) * inv.allocation_ratio) - used,
                }

            candidates.append({
                "request": {rp.uuid: {"resources": allocation_request}},
                "provider_summaries": {rp.uuid: summary},
            })

        if len(candidates) >= limit:
            break

    return {
        "allocation_requests": [c["request"] for c in candidates],
        "provider_summaries": {
            rp_uuid: c["provider_summaries"][rp_uuid]
            for c in candidates
            for rp_uuid in c["provider_summaries"]
        },
    }


# ── Reshaper ──────────────────────────────────────────────────────────────────

class ReshaperIn(BaseModel):
    inventories:  dict[str, dict]   # rp_uuid → {inventories: {...}}
    allocations:  dict[str, dict]   # consumer_uuid → {allocations: {...}, project_id: str}


@app.post("/reshaper", status_code=204)
def reshape(payload: ReshaperIn, _: CurrentUser, db: DB):
    """
    Atomically move inventories and allocations between resource providers.
    Used when restructuring resource providers (e.g., adding nested providers).

    This is a simplified implementation that:
    1. Updates inventories for each specified RP.
    2. Replaces allocations for each specified consumer.
    """
    # Update inventories
    for rp_uuid, inv_data in payload.inventories.items():
        rp = _get_rp(db, rp_uuid)
        new_inventories = inv_data.get("inventories", {})
        # Delete existing
        existing = db.scalars(
            select(Inventory).where(Inventory.resource_provider_id == rp.uuid)
        ).all()
        for inv in existing:
            db.delete(inv)
        # Add new
        for rc, item in new_inventories.items():
            db.add(Inventory(
                resource_provider_id=rp.uuid,
                resource_class=rc,
                total=item.get("total", 0),
                reserved=item.get("reserved", 0),
                min_unit=item.get("min_unit", 1),
                max_unit=item.get("max_unit", item.get("total", 1)),
                step_size=item.get("step_size", 1),
                allocation_ratio=item.get("allocation_ratio", 1.0),
            ))
        rp.generation += 1

    # Replace allocations
    for consumer_uuid, alloc_data in payload.allocations.items():
        # Delete existing for consumer
        existing = db.scalars(
            select(Allocation).where(Allocation.consumer_uuid == consumer_uuid)
        ).all()
        for a in existing:
            db.delete(a)
        # Add new
        project_id = alloc_data.get("project_id", "")
        for rp_uuid, resources in alloc_data.get("allocations", {}).items():
            rp = _get_rp(db, rp_uuid)
            for rc, amount in resources.get("resources", {}).items():
                db.add(Allocation(
                    consumer_uuid=consumer_uuid,
                    project_id=project_id,
                    resource_provider_id=rp.uuid,
                    resource_class=rc,
                    used=amount,
                ))

    db.commit()

