import logging
from shared.db import get_db_url
from shared.config import load_service_config
load_service_config("placement-api")

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

import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated

from shared.auth_middleware import AdminUser, CurrentUser, InternalAuth
from shared.schema import require_schema
from shared.cors import cors_origins
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import (
    text,
    Float, Integer, JSON, String, ForeignKey, DateTime,
    create_engine, delete, select, UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
log = logging.getLogger("placement-api")

DATABASE_URL = get_db_url("cloudx")

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


class ResourceProviderAggregate(Base):
    """Association between a resource provider and an aggregate UUID."""
    __tablename__ = "placement_rp_aggregates"
    resource_provider_id: Mapped[str] = mapped_column(String, ForeignKey("placement_resource_providers.uuid"), primary_key=True)
    aggregate_uuid:       Mapped[str] = mapped_column(String, primary_key=True)


class ComputeNodeHeartbeat(Base):
    """Heartbeat record from compute nodes for multi-node scheduling."""
    __tablename__ = "placement_compute_node_heartbeats"
    resource_provider_id: Mapped[str] = mapped_column(String, ForeignKey("placement_resource_providers.uuid"), primary_key=True)
    host:              Mapped[str] = mapped_column(String, nullable=False)
    status:            Mapped[str] = mapped_column(String, default="active")  # active, down, maintenance
    cpu_percent_used:  Mapped[float | None] = mapped_column(Float, nullable=True)
    memory_mb_used:    Mapped[int | None] = mapped_column(Integer, nullable=True)
    memory_mb_total:   Mapped[int | None] = mapped_column(Integer, nullable=True)
    disk_gb_used:      Mapped[int | None] = mapped_column(Integer, nullable=True)
    disk_gb_total:     Mapped[int | None] = mapped_column(Integer, nullable=True)
    running_vms:       Mapped[int] = mapped_column(Integer, default=0)
    capabilities:      Mapped[dict | None] = mapped_column(JSON, nullable=True)
    last_seen:         Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


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
        rp = ResourceProvider(name="cloudx-compute-0")
        db.add(rp)
        db.commit()
        db.refresh(rp)
    return rp


# ── Bootstrap ──────────────────────────────────────────────────────────────────
def _bootstrap():
    require_schema(engine, "placement")
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


# ── Multi-node Compute Scheduling Helpers ─────────────────────────────────────

def _get_active_compute_nodes(db: Session) -> list[ComputeNodeHeartbeat]:
    """Return compute nodes that have sent heartbeat in the last 60 seconds."""
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=60)
    hbs = db.scalars(
        select(ComputeNodeHeartbeat).where(
            ComputeNodeHeartbeat.status == "active",
            ComputeNodeHeartbeat.last_seen >= cutoff,
        )
    ).all()
    if hbs:
        return list(hbs)
    # Fallback to registered resource providers so compute nodes remain visible
    rps = db.scalars(select(ResourceProvider)).all()
    res = []
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for rp in rps:
        hb = db.get(ComputeNodeHeartbeat, rp.uuid)
        if not hb:
            hb = ComputeNodeHeartbeat(
                resource_provider_id=rp.uuid,
                host=rp.name,
                status="active",
                last_seen=now,
            )
            db.add(hb)
            db.commit()
            db.refresh(hb)
        elif hb.status == "active":
            hb.last_seen = now
            db.commit()
            db.refresh(hb)
        res.append(hb)
    return res


def _get_node_available_resources(db: Session, rp_id: str) -> dict[str, int]:
    """Calculate available resources for a compute node."""
    result = {}
    inventories = db.scalars(
        select(Inventory).where(Inventory.resource_provider_id == rp_id)
    ).all()
    for inv in inventories:
        used = _get_used(db, rp_id, inv.resource_class)
        result[inv.resource_class] = int((inv.total - inv.reserved) * inv.allocation_ratio) - used

    hb = db.scalar(select(ComputeNodeHeartbeat).where(ComputeNodeHeartbeat.resource_provider_id == rp_id))
    if hb:
        if "VCPU" not in result:
            result["VCPU"] = 16
        if "MEMORY_MB" not in result:
            total = hb.memory_mb_total or 32768
            used = hb.memory_mb_used or 0
            result["MEMORY_MB"] = max(0, int(total - used))
        if "DISK_GB" not in result:
            total = hb.disk_gb_total or 500
            used = hb.disk_gb_used or 0
            result["DISK_GB"] = max(0, int(total - used))

    if not result:
        result = {"VCPU": 16, "MEMORY_MB": 32768, "DISK_GB": 500}

    return result


# ── App ────────────────────────────────────────────────────────────────────────
app = FastAPI(title="cloudx placement-api", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
)

try:
    from shared.metrics import setup_metrics
    setup_metrics(app, "placement-api")
except Exception as exc:
    log.warning("metrics endpoint disabled: %s", exc)


@app.on_event("startup")
def startup():
    _bootstrap()


@app.get("/health")
def health():
    return {"status": "ok", "service": "placement-api"}


@app.get("/healthcheck")
def healthcheck(db: DB):
    """Standard CloudX health check endpoint.
    Checks: database connectivity.
    Returns: {status, checks: {db}} compatible with OpenStack-style health format.
    """
    checks = {}
    overall = "up"
    # DB check
    try:
        db.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as exc:
        checks["db"] = f"error: {exc}"
        overall = "degraded"
    return {"status": overall, "checks": checks}



# ── Resource Providers ─────────────────────────────────────────────────────────
@app.post("/internal/resource_providers", status_code=201, dependencies=[InternalAuth])
def create_rp_internal(payload: RPIn, db: DB):
    if db.scalar(select(ResourceProvider).where(ResourceProvider.name == payload.name)):
        raise HTTPException(status_code=409, detail="resource provider already exists")
    if payload.parent_provider_id:
        parent = db.get(ResourceProvider, payload.parent_provider_id) or db.scalar(select(ResourceProvider).where(ResourceProvider.name == payload.parent_provider_id))
        if not parent:
            raise HTTPException(status_code=400, detail=f"parent_provider_id '{payload.parent_provider_id}' not found")
        payload = RPIn(name=payload.name, parent_provider_id=parent.uuid)
    rp = ResourceProvider(**payload.model_dump())
    db.add(rp)
    db.commit()
    db.refresh(rp)
    return _rp_to_dict(rp)


@app.get("/internal/resource_providers", dependencies=[InternalAuth])
def list_rps_internal(
    db: DB,
    in_tree: str | None = None,
    name: str | None = None,
    parent_provider_id: str | None = None,
):
    q = select(ResourceProvider)
    if in_tree:
        root = db.get(ResourceProvider, in_tree) or db.scalar(select(ResourceProvider).where(ResourceProvider.name == in_tree))
        if not root:
            raise HTTPException(status_code=404, detail=f"resource provider tree root '{in_tree}' not found")
        all_rps = db.scalars(select(ResourceProvider)).all()
        tree_uuids = {root.uuid}
        changed = True
        while changed:
            changed = False
            for rp in all_rps:
                if rp.parent_provider_id in tree_uuids and rp.uuid not in tree_uuids:
                    tree_uuids.add(rp.uuid)
                    changed = True
        q = q.where(ResourceProvider.uuid.in_(tree_uuids))
    if name:
        q = q.where(ResourceProvider.name == name)
    if parent_provider_id:
        q = q.where(ResourceProvider.parent_provider_id == parent_provider_id)
    rps = db.scalars(q).all()
    return {"resource_providers": [_rp_to_dict(r) for r in rps]}


@app.post("/resource_providers", status_code=201)
def create_rp(payload: RPIn, _: AdminUser, db: DB):
    return create_rp_internal(payload, db)


@app.get("/resource_providers")
def list_rps(
    _: CurrentUser,
    db: DB,
    in_tree: str | None = None,
    name: str | None = None,
    parent_provider_id: str | None = None,
):
    return list_rps_internal(db, in_tree, name, parent_provider_id)


@app.get("/resource_providers/{rp_uuid}")
def show_rp(rp_uuid: str, _: CurrentUser, db: DB):
    return _rp_to_dict(_get_rp(db, rp_uuid))


# ── Compute Node Heartbeat (Phase 6.1 Multi-Node) ──────────────────────────────

class HeartbeatIn(BaseModel):
    host: str
    cpu_percent_used: float | None = None
    memory_mb_used: int | None = None
    memory_mb_total: int | None = None
    disk_gb_used: int | None = None
    disk_gb_total: int | None = None
    running_vms: int = 0
    capabilities: dict | None = None


@app.post("/resource_providers/{rp_uuid}/heartbeat", status_code=200)
def heartbeat(rp_uuid: str, payload: HeartbeatIn, _: CurrentUser, db: DB):
    """Receive heartbeat from a compute node worker."""
    rp = _get_rp(db, rp_uuid)
    hb = db.get(ComputeNodeHeartbeat, rp.uuid)
    now = utcnow()
    if not hb:
        hb = ComputeNodeHeartbeat(
            resource_provider_id=rp.uuid,
            host=payload.host,
            status="active",
        )
        db.add(hb)
    
    hb.host = payload.host
    hb.cpu_percent_used = payload.cpu_percent_used
    hb.memory_mb_used = payload.memory_mb_used
    hb.memory_mb_total = payload.memory_mb_total
    hb.disk_gb_used = payload.disk_gb_used
    hb.disk_gb_total = payload.disk_gb_total
    hb.running_vms = payload.running_vms
    hb.capabilities = payload.capabilities
    hb.last_seen = now
    hb.status = "active"
    
    db.commit()
    return {"status": "ok", "resource_provider_uuid": rp.uuid, "timestamp": now.isoformat()}


@app.get("/resource_providers/{rp_uuid}/heartbeat")
def get_heartbeat(rp_uuid: str, _: CurrentUser, db: DB):
    """Get last heartbeat for a compute node."""
    rp = _get_rp(db, rp_uuid)
    hb = db.get(ComputeNodeHeartbeat, rp.uuid)
    if not hb:
        raise HTTPException(status_code=404, detail="heartbeat not found")
    return {
        "resource_provider_uuid": rp.uuid,
        "host": hb.host,
        "status": hb.status,
        "cpu_percent_used": hb.cpu_percent_used,
        "memory_mb_used": hb.memory_mb_used,
        "memory_mb_total": hb.memory_mb_total,
        "disk_gb_used": hb.disk_gb_used,
        "disk_gb_total": hb.disk_gb_total,
        "running_vms": hb.running_vms,
        "capabilities": hb.capabilities,
        "last_seen": hb.last_seen.isoformat() if hb.last_seen else None,
    }


@app.get("/compute_nodes")
def list_compute_nodes(_: CurrentUser, db: DB):
    """List all active compute nodes with their current status."""
    nodes = _get_active_compute_nodes(db)
    result = []
    for hb in nodes:
        rp = db.get(ResourceProvider, hb.resource_provider_id)
        available = _get_node_available_resources(db, hb.resource_provider_id)
        result.append({
            "resource_provider_uuid": hb.resource_provider_id,
            "name": rp.name if rp else None,
            "host": hb.host,
            "status": hb.status,
            "cpu_percent_used": hb.cpu_percent_used,
            "memory_mb_used": hb.memory_mb_used,
            "memory_mb_total": hb.memory_mb_total,
            "available_resources": available,
            "running_vms": hb.running_vms,
            "last_seen": hb.last_seen.isoformat() if hb.last_seen else None,
        })
    return {"compute_nodes": result}


@app.delete("/resource_providers/{rp_uuid}", status_code=204)
def delete_rp(rp_uuid: str, _: AdminUser, db: DB):
    rp = _get_rp(db, rp_uuid)
    # like OpenStack placement: a provider that still serves instances or has child providers cannot be deleted
    if db.scalar(select(Allocation.id).where(Allocation.resource_provider_id == rp.uuid).limit(1)):
        raise HTTPException(status_code=409, detail="resource provider has allocations")
    if db.scalar(select(ResourceProvider.uuid).where(ResourceProvider.parent_provider_id == rp.uuid).limit(1)):
        raise HTTPException(status_code=409, detail="resource provider has child providers")
    # its own inventories, traits, aggregates and heartbeat go with it (they reference the provider)
    for model in (Inventory, ResourceProviderTrait, ResourceProviderAggregate, ComputeNodeHeartbeat):
        db.execute(delete(model).where(model.resource_provider_id == rp.uuid))
    db.delete(rp)
    db.commit()


# ── Inventories ────────────────────────────────────────────────────────────────
@app.put("/resource_providers/{rp_uuid}/inventories")
def set_inventories(rp_uuid: str, payload: InventoriesIn, _: AdminUser, db: DB):
    rp = _get_rp(db, rp_uuid)
    # Delete existing inventories
    existing = db.scalars(select(Inventory).where(Inventory.resource_provider_id == rp.uuid)).all()
    for inv in existing:
        db.delete(inv)
    db.flush()  # the unit of work INSERTs before it DELETEs; flush first or the unique key trips
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


# ── Custom Resource Classes ────────────────────────────────────────────────────
# OpenStack supports custom resource classes prefixed with CUSTOM_
# See: https://docs.openstack.org/placement/latest/user/resource-classes.html

_STANDARD_RESOURCE_CLASSES = {
    "VCPU", "MEMORY_MB", "DISK_GB", "NETWORK_BANDWIDTH_EGRESS_KIBIBYTES_PER_SECOND",
    "NETWORK_BANDWIDTH_INGRESS_KIBIBYTES_PER_SECOND", "PCI_DEVICE", "SRIOV_NET_VF",
    "NUMA_CORE", "NUMA_MEMORY_MB", "NUMA_SOCKET", "NUMA_THREAD",
    "NET_BW_EGR_KILOBIT_PER_SEC", "NET_BW_IGR_KILOBIT_PER_SEC",
    "FPGA", "GPU", "IPV4_ADDRESS", "VGPU", "VGPU_DISPLAY_HEAD",
}


class CustomResourceClass(Base):
    """User-defined resource classes (must be prefixed with CUSTOM_)."""
    __tablename__ = "placement_custom_resource_classes"
    name:       Mapped[str] = mapped_column(String, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


@app.get("/resource_classes")
def list_resource_classes(_: CurrentUser, db: DB):
    """List all resource classes (standard + custom)."""
    custom = db.scalars(select(CustomResourceClass)).all()
    custom_names = [rc.name for rc in custom]
    all_classes = sorted(_STANDARD_RESOURCE_CLASSES | set(custom_names))
    return {
        "resource_classes": [
            {"name": name, "standard": name in _STANDARD_RESOURCE_CLASSES}
            for name in all_classes
        ]
    }


@app.put("/resource_classes/{name}", status_code=201)
def create_or_update_resource_class(name: str, _: CurrentUser, db: DB):
    """
    Create a custom resource class. Name must start with 'CUSTOM_'.
    Standard classes cannot be created via this endpoint.
    """
    if not name.startswith("CUSTOM_"):
        raise HTTPException(
            status_code=400,
            detail="Custom resource class names must start with 'CUSTOM_'",
        )
    if not name.replace("CUSTOM_", "").replace("_", "").isalnum():
        raise HTTPException(
            status_code=400,
            detail="Resource class name may only contain letters, digits, and underscores after 'CUSTOM_'",
        )
    existing = db.get(CustomResourceClass, name)
    if existing:
        return {"name": name, "created": False, "message": "resource class already exists"}
    db.add(CustomResourceClass(name=name))
    db.commit()
    return {"name": name, "created": True}


@app.get("/resource_classes/{name}")
def show_resource_class(name: str, _: CurrentUser, db: DB):
    """Show a specific resource class (standard or custom)."""
    if name in _STANDARD_RESOURCE_CLASSES:
        return {"name": name, "standard": True}
    rc = db.get(CustomResourceClass, name)
    if not rc:
        raise HTTPException(status_code=404, detail=f"resource class '{name}' not found")
    return {"name": rc.name, "standard": False, "created_at": rc.created_at.isoformat()}


@app.delete("/resource_classes/{name}", status_code=204)
def delete_resource_class(name: str, _: CurrentUser, db: DB):
    """Delete a custom resource class. Standard classes cannot be deleted."""
    if name in _STANDARD_RESOURCE_CLASSES:
        raise HTTPException(status_code=400, detail="Cannot delete a standard resource class")
    rc = db.get(CustomResourceClass, name)
    if not rc:
        raise HTTPException(status_code=404, detail=f"resource class '{name}' not found")
    db.delete(rc)
    db.commit()




# ── Aggregates ────────────────────────────────────────────────────────────────
@app.get("/resource_providers/{rp_uuid}/aggregates")
def get_aggregates(rp_uuid: str, _: CurrentUser, db: DB):
    """List aggregate UUIDs associated with a resource provider."""
    rp = _get_rp(db, rp_uuid)
    aggs = db.scalars(
        select(ResourceProviderAggregate).where(ResourceProviderAggregate.resource_provider_id == rp.uuid)
    ).all()
    return {"aggregates": [a.aggregate_uuid for a in aggs], "resource_provider_generation": rp.generation}


@app.put("/resource_providers/{rp_uuid}/aggregates")
def set_aggregates(rp_uuid: str, payload: dict, _: CurrentUser, db: DB):
    """Set the aggregate UUIDs for a resource provider (replaces existing)."""
    rp = _get_rp(db, rp_uuid)
    aggregate_uuids = payload.get("aggregates", [])
    if not isinstance(aggregate_uuids, list):
        raise HTTPException(status_code=400, detail="'aggregates' must be a list of UUIDs")
    # Delete existing
    existing = db.scalars(
        select(ResourceProviderAggregate).where(ResourceProviderAggregate.resource_provider_id == rp.uuid)
    ).all()
    for a in existing:
        db.delete(a)
    # Add new
    for agg_uuid in aggregate_uuids:
        db.add(ResourceProviderAggregate(resource_provider_id=rp.uuid, aggregate_uuid=str(agg_uuid)))
    rp.generation += 1
    db.commit()
    return {"aggregates": aggregate_uuids, "resource_provider_generation": rp.generation}


# ── Allocations ───────────────────────────────────────────────────────────────
@app.post("/allocations", status_code=201)
def create_allocations(payload: AllocationIn, _: AdminUser, db: DB):
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


class ClaimIn(BaseModel):
    consumer_uuid: str
    project_id: str
    resources: dict[str, int]
    exclude_hosts: list[str] = []
    only_hosts: list[str] | None = None


@app.post("/internal/claim", dependencies=[InternalAuth])
def claim(payload: ClaimIn, db: DB):
    """Scheduler: pick the provider with most free MEMORY_MB that fits every requested class, and allocate there."""
    best, best_free, seen_inventory = None, -1, False
    for rp in db.scalars(select(ResourceProvider)):
        inv = {i.resource_class: i for i in db.scalars(select(Inventory).where(Inventory.resource_provider_id == rp.uuid))}
        if not inv:
            continue
        seen_inventory = True  # before the host filters: a node that anti-affinity excluded still reported inventory
        if rp.name in payload.exclude_hosts or (payload.only_hosts is not None and rp.name not in payload.only_hosts):
            continue
        free = {rc: int((i.total - i.reserved) * i.allocation_ratio) - _get_used(db, rp.uuid, rc) for rc, i in inv.items()}
        if all(rc in free and free[rc] >= amt for rc, amt in payload.resources.items()):
            if free.get("MEMORY_MB", 0) > best_free:
                best, best_free = rp, free.get("MEMORY_MB", 0)
    if not best:
        # 503 = nothing reports inventory yet (caller falls back); 409 = real capacity/filter failure
        raise HTTPException(status_code=409 if seen_inventory else 503, detail="No valid host: insufficient resources or filtered out")
    for a in db.scalars(select(Allocation).where(Allocation.consumer_uuid == payload.consumer_uuid)):
        db.delete(a)
    for rc, amt in payload.resources.items():
        db.add(Allocation(consumer_uuid=payload.consumer_uuid, project_id=payload.project_id,
                          resource_provider_id=best.uuid, resource_class=rc, used=amt))
    db.commit()
    return {"host": best.name, "resource_provider": best.uuid}


@app.delete("/internal/allocations/{consumer_uuid}", status_code=204, dependencies=[InternalAuth])
def release_allocations(consumer_uuid: str, db: DB):
    for a in db.scalars(select(Allocation).where(Allocation.consumer_uuid == consumer_uuid)):
        db.delete(a)
    db.commit()


@app.put("/internal/resource_providers/{name}/inventories", dependencies=[InternalAuth])
def set_inventories_internal(name: str, payload: InventoriesIn, db: DB):
    return set_inventories(name, payload, None, db)


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
def delete_allocations(consumer_uuid: str, _: AdminUser, db: DB):
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
    target_project = project_id
    if not target_project or current.get("role") != "admin":
        target_project = project_id if (current.get("role") == "admin" and project_id) else current["project_id"]

    q = select(Allocation).where(Allocation.project_id == target_project)

    allocations = db.scalars(q).all()
    usages: dict[str, int] = {}
    consumer_ids = set()
    for a in allocations:
        usages[a.resource_class] = usages.get(a.resource_class, 0) + a.used
        consumer_ids.add(a.consumer_uuid)

    usages["instances"] = len(consumer_ids)

    return {"usages": usages, "project_id": target_project}


@app.get("/resource_providers/{rp_uuid}/usages")
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
    response.headers["X-Compute-API-Version"] = PLACEMENT_CURRENT_VERSION
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
    required: str | None = None,   # comma-sep trait names that RP MUST have
    forbidden: str | None = None,  # comma-sep trait names that RP MUST NOT have
):
    """
    Find resource providers that can satisfy the given resource request.

    Query parameters:
      resources — RESOURCE_CLASS:amount[,RESOURCE_CLASS:amount]
                  Example: VCPU:2,MEMORY_MB:4096,DISK_GB:50
      required  — comma-separated trait names the RP must have (AND logic)
                  Example: HW_CPU_X86_AVX2
      forbidden — comma-separated trait names the RP must NOT have (AND logic)
                  Example: COMPUTE_TRUSTED_CERTS

    Returns a list of candidate provider summaries and allocation requests.
    """
    # Parse resources string
    requested: dict[str, int] = {}
    try:
        for part in resources.split(","):
            rc, amount = part.strip().split(":")
            requested[rc.strip()] = int(amount.strip())
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid resources format. Use RESOURCE_CLASS:amount[,...]")

    required_traits: set[str] = set()
    forbidden_traits: set[str] = set()
    if required:
        required_traits = {t.strip() for t in required.split(",") if t.strip()}
    if forbidden:
        forbidden_traits = {t.strip() for t in forbidden.split(",") if t.strip()}

    rps = db.scalars(select(ResourceProvider)).all()
    candidates = []

    for rp in rps:
        # ── Trait filtering (required / forbidden) ─────────────────────────
        if required_traits or forbidden_traits:
            rp_traits = {
                t.trait_name for t in db.scalars(
                    select(ResourceProviderTrait).where(
                        ResourceProviderTrait.resource_provider_id == rp.uuid
                    )
                ).all()
            }
            # All required traits must be present
            if not required_traits.issubset(rp_traits):
                continue
            # None of the forbidden traits may be present
            if rp_traits & forbidden_traits:
                continue

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


# ── Multi-Node Scheduling (Phase 6.1) ─────────────────────────────────────────

class ScheduleRequest(BaseModel):
    resources: dict[str, int]  # RESOURCE_CLASS: amount
    strategy: str = "memory"   # memory | cpu | spread | packed
    required_traits: list[str] | None = None
    forbidden_traits: list[str] | None = None


@app.post("/scheduler/select_destination")
def select_destination(payload: ScheduleRequest, _: CurrentUser, db: DB):
    """Select best compute node for a new server based on scheduling strategy."""
    nodes = _get_active_compute_nodes(db)
    if not nodes:
        # Fallback to default RP if no heartbeats registered
        default_rp = _get_or_create_default_rp(db)
        return {
            "selected_host": default_rp.name,
            "resource_provider_uuid": default_rp.uuid,
            "reason": "fallback_no_heartbeat",
        }
    
    candidates = []
    for hb in nodes:
        rp = db.get(ResourceProvider, hb.resource_provider_id)
        if not rp:
            continue
        
        # Trait filtering
        if payload.required_traits or payload.forbidden_traits:
            rp_traits = {
                t.trait_name for t in db.scalars(
                    select(ResourceProviderTrait).where(
                        ResourceProviderTrait.resource_provider_id == rp.uuid
                    )
                ).all()
            }
            required = set(payload.required_traits or [])
            forbidden = set(payload.forbidden_traits or [])
            if not required.issubset(rp_traits):
                continue
            if rp_traits & forbidden:
                continue
        
        available = _get_node_available_resources(db, rp.uuid)
        can_fit = all(available.get(rc, 0) >= amount for rc, amount in payload.resources.items())
        if not can_fit:
            continue
        
        candidates.append({
            "rp": rp,
            "hb": hb,
            "available": available,
        })
    
    if not candidates:
        raise HTTPException(status_code=409, detail="No compute node can satisfy resource request")
    
    # Sort by strategy
    strategy = payload.strategy.lower()
    if strategy == "memory":
        candidates.sort(key=lambda c: c["available"].get("MEMORY_MB", 0))
    elif strategy == "cpu":
        candidates.sort(key=lambda c: c["available"].get("VCPU", 0))
    elif strategy == "spread":
        # Prefer node with fewest running VMs
        candidates.sort(key=lambda c: c["hb"].running_vms)
    elif strategy == "packed":
        # Prefer node with most used resources (fill up first)
        candidates.sort(key=lambda c: c["hb"].running_vms, reverse=True)
    else:
        # Default: memory weighted
        candidates.sort(key=lambda c: c["available"].get("MEMORY_MB", 0))
    
    selected = candidates[0]
    return {
        "selected_host": selected["hb"].host,
        "resource_provider_uuid": selected["rp"].uuid,
        "available_resources": selected["available"],
        "running_vms": selected["hb"].running_vms,
        "strategy": strategy,
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

