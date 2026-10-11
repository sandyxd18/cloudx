from __future__ import annotations
from shared.db import get_db_url
from shared.config import load_service_config
load_service_config("orchestration-api")

"""
cloudx Orchestration API — Auto-Scaling Group Engine
======================================================

Independent microservice (Phase 6.2) that manages Auto-Scaling Groups:
  • Scaling Group definition (min/max instances, target CPU, cooldown)
  • Integration with compute-api, load_balancer, telemetry-api
  • Manual scale operations

Database: cloudx_orchestration
"""


import logging
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Integer, String, create_engine, select
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from shared.auth_middleware import CurrentUser, UserOrInternal
from shared.schema import require_schema

from shared.request_id_middleware import RequestIDMiddleware
from shared.cors import cors_origins

log = logging.getLogger("orchestration-api")

DATABASE_URL = get_db_url("cloudx_orchestration", "ORCHESTRATION_DATABASE_URL")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


class ScalingGroup(Base):
    __tablename__ = "orchestration_scaling_groups"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    min_instances: Mapped[int] = mapped_column(Integer, default=1)
    max_instances: Mapped[int] = mapped_column(Integer, default=10)
    target_cpu_percent: Mapped[int] = mapped_column(Integer, default=70)
    cooldown_seconds: Mapped[int] = mapped_column(Integer, default=300)
    scale_in_cooldown_seconds: Mapped[int] = mapped_column(Integer, default=600)
    launch_template_id: Mapped[str] = mapped_column(String(64), nullable=False)
    image_id: Mapped[str] = mapped_column(String(64), nullable=False)
    flavor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    network_id: Mapped[str] = mapped_column(String(64), nullable=False)
    load_balancer_pool_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    subnet_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(16), default="active")
    last_scale_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_scale_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    instances: Mapped[list["ScalingGroupInstance"]] = relationship("ScalingGroupInstance", back_populates="group", cascade="all, delete-orphan")


class ScalingGroupInstance(Base):
    __tablename__ = "orchestration_scaling_group_instances"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    group_id: Mapped[str] = mapped_column(String(64), ForeignKey("orchestration_scaling_groups.id"), nullable=False, index=True)
    server_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    server_name: Mapped[str] = mapped_column(String(128), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    port_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    group: Mapped["ScalingGroup"] = relationship("ScalingGroup", back_populates="instances")


class ScalingHistory(Base):
    __tablename__ = "orchestration_scaling_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(16), nullable=False)  # scale_out, scale_in
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    server_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    avg_cpu_before: Mapped[float | None] = mapped_column(nullable=True)
    instance_count_before: Mapped[int | None] = mapped_column(Integer, nullable=True)
    instance_count_after: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


DB = Annotated[Session, Depends(get_db)]


def init_db():
    require_schema(engine, "orchestration")


app = FastAPI(
    title="CloudX Orchestration API",
    description="Auto-Scaling Group Engine for CloudX",
    version="6.2.0",
)

if RequestIDMiddleware:
    app.add_middleware(RequestIDMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    init_db()
    with SessionLocal() as db:
        stacks.recover_stuck_stacks(db)
    log.info("Orchestration API started")


# ── Pydantic models ───────────────────────────────────────────────────────────

class ScalingGroupIn(BaseModel):
    name: str
    description: str | None = None
    min_instances: int = 1
    max_instances: int = 10
    target_cpu_percent: int = 70
    cooldown_seconds: int = 300
    scale_in_cooldown_seconds: int = 600
    launch_template_id: str = "default"
    image_id: str
    flavor_id: str
    network_id: str
    subnet_id: str | None = None
    load_balancer_pool_id: str | None = None


class ScalingGroupUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    min_instances: int | None = None
    max_instances: int | None = None
    target_cpu_percent: int | None = None
    cooldown_seconds: int | None = None
    scale_in_cooldown_seconds: int | None = None


class ScalingGroupInstanceOut(BaseModel):
    id: str
    group_id: str
    server_id: str
    server_name: str
    ip_address: str | None
    port_id: str | None
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


class ScalingGroupOut(BaseModel):
    id: str
    project_id: str
    name: str
    description: str | None
    min_instances: int
    max_instances: int
    target_cpu_percent: int
    cooldown_seconds: int
    scale_in_cooldown_seconds: int
    launch_template_id: str
    image_id: str
    flavor_id: str
    network_id: str
    subnet_id: str | None
    load_balancer_pool_id: str | None
    is_active: bool
    status: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ScalingHistoryOut(BaseModel):
    id: int
    group_id: str
    action: str
    reason: str | None
    server_id: str | None
    avg_cpu_before: float | None
    instance_count_before: int | None
    instance_count_after: int | None
    created_at: datetime

    class Config:
        from_attributes = True


# ── Endpoints ─────────────────────────────────────────────────────────────────

def _compute(request: Request | None, method: str, path: str, body: dict | None = None) -> dict:
    """Call compute-api as the caller, so a manual scale really creates or deletes the VM."""
    from .engine import Api, ResourceFailure
    if request is None or not request.headers.get("Authorization"):
        raise HTTPException(status_code=401, detail="Authorization header required")
    try:
        return Api(request.headers["Authorization"]).call("compute", method, path, body, ok=(200, 201, 202, 204, 404))
    except ResourceFailure as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _own_group(db, user, group_id: str) -> ScalingGroup:
    group = db.get(ScalingGroup, group_id)
    if not group or (user.get("role") != "admin" and group.project_id != user.get("project_id")):
        raise HTTPException(status_code=404, detail="Scaling group not found")
    return group


@app.post("/scaling-groups", response_model=ScalingGroupOut, status_code=status.HTTP_201_CREATED)
@app.post("/orchestration/scaling-groups", response_model=ScalingGroupOut, status_code=status.HTTP_201_CREATED)
def create_scaling_group(user: CurrentUser, data: ScalingGroupIn, db: DB):
    pid = user.get("project_id", "admin") if isinstance(user, dict) else getattr(user, "project_id", "admin")
    group = ScalingGroup(
        project_id=pid or "admin",
        **data.model_dump(),
    )
    db.add(group)
    db.commit()
    db.refresh(group)
    return group


@app.get("/scaling-groups", response_model=list[ScalingGroupOut])
@app.get("/orchestration/scaling-groups", response_model=list[ScalingGroupOut])
def list_scaling_groups(user: UserOrInternal, db: DB, project_id: str | None = None, active_only: bool = False):
    q = select(ScalingGroup)
    if user.get("role") != "admin" or project_id:  # admin (the worker) sees all projects unless it filters
        q = q.where(ScalingGroup.project_id == (project_id or user.get("project_id", "admin")))
    if active_only:
        q = q.where(ScalingGroup.is_active.is_(True))
    return db.execute(q).scalars().all()


@app.get("/scaling-groups/{group_id}", response_model=ScalingGroupOut)
@app.get("/orchestration/scaling-groups/{group_id}", response_model=ScalingGroupOut)
def get_scaling_group(user: CurrentUser, group_id: str, db: DB):
    group = _own_group(db, user, group_id)
    return group


@app.patch("/scaling-groups/{group_id}", response_model=ScalingGroupOut)
@app.patch("/orchestration/scaling-groups/{group_id}", response_model=ScalingGroupOut)
def update_scaling_group(user: CurrentUser, group_id: str, data: ScalingGroupUpdate, db: DB):
    group = _own_group(db, user, group_id)
    for field, val in data.model_dump(exclude_unset=True).items():
        if val is not None:
            setattr(group, field, val)
    db.commit()
    db.refresh(group)
    return group


@app.delete("/scaling-groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
@app.delete("/orchestration/scaling-groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_scaling_group(user: CurrentUser, group_id: str, db: DB):
    group = _own_group(db, user, group_id)
    db.delete(group)
    db.commit()
    return None


@app.post("/scaling-groups/{group_id}/scale-out", response_model=ScalingGroupInstanceOut)
@app.post("/orchestration/scaling-groups/{group_id}/scale-out", response_model=ScalingGroupInstanceOut)
def manual_scale_out(user: CurrentUser, group_id: str, db: DB, request: Request = None):
    group = _own_group(db, user, group_id)

    count_before = len(group.instances)
    if count_before >= group.max_instances:
        raise HTTPException(status_code=400, detail="Scaling group already at max_instances limit")

    server_name = f"{group.name}-asg-{count_before + 1}"
    server_id = _compute(request, "POST", "/servers", {"name": server_name, "image": group.image_id,
                                                      "flavor": group.flavor_id, "network": group.network_id})["id"]
    inst = ScalingGroupInstance(
        group_id=group.id,
        server_id=server_id,
        server_name=server_name,
        status="active",
    )
    group.last_scale_out_at = datetime.now(timezone.utc)
    db.add(inst)

    history = ScalingHistory(
        group_id=group.id,
        action="scale_out",
        reason="Manual scale-out requested",
        server_id=server_id,
        instance_count_before=count_before,
        instance_count_after=count_before + 1,
    )
    db.add(history)
    db.commit()
    db.refresh(inst)
    return inst


@app.post("/scaling-groups/{group_id}/scale-in", status_code=status.HTTP_200_OK)
@app.post("/orchestration/scaling-groups/{group_id}/scale-in", status_code=status.HTTP_200_OK)
def manual_scale_in(user: CurrentUser, group_id: str, db: DB, request: Request = None):
    group = _own_group(db, user, group_id)

    count_before = len(group.instances)
    if count_before <= group.min_instances:
        raise HTTPException(status_code=400, detail="Scaling group already at min_instances limit")

    inst_to_remove = group.instances[-1]
    server_id = inst_to_remove.server_id
    _compute(request, "DELETE", f"/servers/{server_id}")
    db.delete(inst_to_remove)

    group.last_scale_in_at = datetime.now(timezone.utc)

    history = ScalingHistory(
        group_id=group.id,
        action="scale_in",
        reason="Manual scale-in requested",
        server_id=server_id,
        instance_count_before=count_before,
        instance_count_after=count_before - 1,
    )
    db.add(history)
    db.commit()
    return {"message": f"Scaled in server {server_id}", "remaining_instances": count_before - 1}


@app.get("/scaling-groups/{group_id}/instances", response_model=list[ScalingGroupInstanceOut])
@app.get("/orchestration/scaling-groups/{group_id}/instances", response_model=list[ScalingGroupInstanceOut])
def list_group_instances(user: UserOrInternal, group_id: str, db: DB):
    group = _own_group(db, user, group_id)
    return group.instances


@app.get("/scaling-groups/{group_id}/history", response_model=list[ScalingHistoryOut])
@app.get("/orchestration/scaling-groups/{group_id}/history", response_model=list[ScalingHistoryOut])
def get_scaling_history(user: CurrentUser, group_id: str, db: DB):
    _own_group(db, user, group_id)
    q = select(ScalingHistory).where(ScalingHistory.group_id == group_id).order_by(ScalingHistory.created_at.desc())
    return db.execute(q).scalars().all()


class ScalingEventIn(BaseModel):
    action: str = Field(pattern="^(scale_out|scale_in)$")
    reason: str | None = None
    server_id: str | None = None
    server_name: str | None = None
    avg_cpu_before: float | None = None
    instance_count_before: int | None = None
    instance_count_after: int | None = None


@app.post("/scaling-groups/{group_id}/history", status_code=status.HTTP_201_CREATED)
@app.post("/orchestration/scaling-groups/{group_id}/history", status_code=status.HTTP_201_CREATED)
def record_scaling_event(admin: UserOrInternal, group_id: str, data: ScalingEventIn, db: DB):
    """Called by orchestration-worker after it scaled: logs history, syncs instances and cooldown timestamps."""
    group = _own_group(db, admin, group_id)
    now = datetime.now(timezone.utc)
    if data.action == "scale_out":
        group.last_scale_out_at = now
        if data.server_id:
            db.add(ScalingGroupInstance(group_id=group.id, server_id=data.server_id,
                                        server_name=data.server_name or data.server_id, status="active"))
    else:
        group.last_scale_in_at = now
        if data.server_id:
            for inst in [i for i in group.instances if i.server_id == data.server_id]:
                db.delete(inst)
    db.add(ScalingHistory(group_id=group.id, **data.model_dump(exclude={"server_name"})))
    db.commit()
    return {"status": "recorded"}


from . import stacks  # noqa: E402  (stacks imports Base/DB from here, so it must come last)
app.include_router(stacks.router)

try:
    from shared.metrics import setup_metrics
    setup_metrics(app, "orchestration-api")
except Exception:  # best-effort: /metrics is optional, the API must still start
    pass

@app.get("/health")
def health_check():
    return {"status": "ok", "service": "orchestration-api"}
