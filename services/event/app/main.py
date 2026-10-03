from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
try:
    from shared.config import load_service_config
    load_service_config("event-api")
except Exception as _cfg_err:
    pass

"""
cloudx Event & Webhook API — Central Event Bus & Notification Engine
===================================================================

Service Fase 5.3: menangkap event audit/system dan mengirim webhook.
"""


import logging
import os
import sys
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Integer, String, Text,
    create_engine, select, inspect
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

sys.path.insert(0, "/app")
from shared.auth_middleware import CurrentUser, AdminUser, OptionalAuth

try:
    from shared.request_id_middleware import RequestIDMiddleware
except ImportError:
    RequestIDMiddleware = None

log = logging.getLogger("event-api")

DATABASE_URL = os.getenv("EVENT_DATABASE_URL", os.getenv("DATABASE_URL", "postgresql+psycopg://mini:mini@postgres:5432/cloudx_events"))

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
inspector = inspect(engine)


class Base(DeclarativeBase):
    pass


class WebhookSubscription(Base):
    __tablename__ = "event_webhook_subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    endpoint_url: Mapped[str] = mapped_column(String(512), nullable=False)
    event_types: Mapped[str] = mapped_column(String(512), nullable=False)  # comma separated or json
    secret: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    deliveries: Mapped[list["WebhookDelivery"]] = relationship("WebhookDelivery", back_populates="subscription", cascade="all, delete-orphan")


class EventLog(Base):
    __tablename__ = "event_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    resource_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    project_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)


class WebhookDelivery(Base):
    __tablename__ = "event_webhook_deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subscription_id: Mapped[int] = mapped_column(Integer, ForeignKey("event_webhook_subscriptions.id"), nullable=False)
    event_id: Mapped[int] = mapped_column(Integer, ForeignKey("event_logs.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending, success, failed
    response_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    subscription: Mapped["WebhookSubscription"] = relationship("WebhookSubscription", back_populates="deliveries")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


DB = Annotated[Session, Depends(get_db)]


def init_db():
    tables = ["event_webhook_subscriptions", "event_logs", "event_webhook_deliveries"]
    if not all(inspector.has_table(t) for t in tables):
        Base.metadata.create_all(bind=engine)
        log.info("Event tables created")


app = FastAPI(
    title="CloudX Event API",
    description="Central Event Bus & Webhook Notification Engine for CloudX",
    version="5.3.0",
)

if RequestIDMiddleware:
    app.add_middleware(RequestIDMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    init_db()
    log.info("Event API started")


# ── Pydantic models ───────────────────────────────────────────────────────────

class WebhookSubscriptionIn(BaseModel):
    name: str
    endpoint_url: str
    event_types: list[str]
    secret: str | None = None
    is_active: bool = True

    @field_validator("endpoint_url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        return v


class WebhookSubscriptionOut(BaseModel):
    id: int
    project_id: str
    name: str
    endpoint_url: str
    event_types: str
    secret: str | None
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class EventEmitIn(BaseModel):
    event_type: str
    resource_type: str = "instance"
    resource_id: str | None = None
    project_id: str | None = None
    user_id: str | None = None
    payload: dict | str | None = None


class EventLogOut(BaseModel):
    id: int
    event_type: str
    resource_type: str
    resource_id: str | None
    project_id: str | None
    user_id: str | None
    payload: str | None
    timestamp: datetime

    class Config:
        from_attributes = True


class WebhookDeliveryOut(BaseModel):
    id: int
    subscription_id: int
    event_id: int
    status: str
    response_code: int | None
    attempts: int
    created_at: datetime

    class Config:
        from_attributes = True


# ── API Endpoints ─────────────────────────────────────────────────────────────

@app.post("/events/emit", response_model=EventLogOut, status_code=status.HTTP_201_CREATED)
def emit_event(user: CurrentUser, data: EventEmitIn, db: DB):
    payload_str = str(data.payload) if isinstance(data.payload, dict) else data.payload
    pid = data.project_id or (user.get("project_id") if isinstance(user, dict) else getattr(user, "project_id", "admin")) or "admin"
    uid = data.user_id or (user.get("user_id") if isinstance(user, dict) else getattr(user, "user_id", "system")) or "system"
    log_entry = EventLog(
        event_type=data.event_type,
        resource_type=data.resource_type,
        resource_id=data.resource_id,
        project_id=pid,
        user_id=uid,
        payload=payload_str,
    )
    db.add(log_entry)
    db.commit()
    db.refresh(log_entry)

    # Queue matching webhooks
    subs = db.execute(
        select(WebhookSubscription).where(
            WebhookSubscription.is_active == True,
            WebhookSubscription.project_id == pid,
        )
    ).scalars().all()

    for sub in subs:
        types = [t.strip() for t in sub.event_types.split(",")]
        if "*" in types or data.event_type in types:
            delivery = WebhookDelivery(
                subscription_id=sub.id,
                event_id=log_entry.id,
                status="pending",
            )
            db.add(delivery)
    db.commit()

    return log_entry


@app.post("/events/deliver", status_code=status.HTTP_202_ACCEPTED)
def deliver_event(user: CurrentUser, data: EventEmitIn, db: DB):
    log_entry = emit_event(user, data, db)
    return {"event_id": log_entry.id, "status": "queued"}


@app.get("/events/types")
def list_event_types(user: OptionalAuth):
    types = [
        {"type": "instance.created", "event_type": "instance.created", "description": "Instance creation event"},
        {"type": "instance.crashed", "event_type": "instance.crashed", "description": "Instance crash event"},
        {"type": "instance.deleted", "event_type": "instance.deleted", "description": "Instance deletion event"},
        {"type": "volume.created", "event_type": "volume.created", "description": "Volume creation event"},
        {"type": "network.floating_ip.assigned", "event_type": "network.floating_ip.assigned", "description": "Floating IP assigned"},
    ]
    return {"event_types": types}


@app.get("/events/logs", response_model=list[EventLogOut])
def list_event_logs(
    user: CurrentUser,
    db: DB,
    event_type: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    project_id: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
):
    q = select(EventLog)
    if event_type:
        q = q.where(EventLog.event_type == event_type)
    if resource_type:
        q = q.where(EventLog.resource_type == resource_type)
    if resource_id:
        q = q.where(EventLog.resource_id == resource_id)
    if project_id:
        q = q.where(EventLog.project_id == project_id)
    q = q.order_by(EventLog.timestamp.desc()).limit(limit)
    return db.execute(q).scalars().all()


@app.post("/events/subscriptions", response_model=WebhookSubscriptionOut, status_code=status.HTTP_201_CREATED)
def create_subscription(user: CurrentUser, data: WebhookSubscriptionIn, db: DB):
    pid = user.get("project_id", "admin") if isinstance(user, dict) else getattr(user, "project_id", "admin")
    event_types_str = ",".join(data.event_types)
    sub = WebhookSubscription(
        project_id=pid or "admin",
        name=data.name,
        endpoint_url=data.endpoint_url,
        event_types=event_types_str,
        secret=data.secret,
        is_active=data.is_active,
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return sub


@app.get("/events/subscriptions", response_model=list[WebhookSubscriptionOut])
def list_subscriptions(user: CurrentUser, db: DB, project_id: str | None = None):
    pid = project_id or getattr(user, "project_id", "admin")
    q = select(WebhookSubscription).where(WebhookSubscription.project_id == pid)
    return db.execute(q).scalars().all()


@app.get("/events/subscriptions/{sub_id}", response_model=WebhookSubscriptionOut)
def get_subscription(user: CurrentUser, sub_id: int, db: DB):
    sub = db.get(WebhookSubscription, sub_id)
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")
    return sub


@app.delete("/events/subscriptions/{sub_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_subscription(user: CurrentUser, sub_id: int, db: DB):
    sub = db.get(WebhookSubscription, sub_id)
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")
    db.delete(sub)
    db.commit()
    return None


@app.get("/events/deliveries", response_model=list[WebhookDeliveryOut])
def list_deliveries(user: CurrentUser, db: DB, subscription_id: int | None = None, status: str | None = None):
    q = select(WebhookDelivery)
    if subscription_id:
        q = q.where(WebhookDelivery.subscription_id == subscription_id)
    if status:
        q = q.where(WebhookDelivery.status == status)
    q = q.order_by(WebhookDelivery.created_at.desc())
    return db.execute(q).scalars().all()


@app.post("/events/deliveries/{delivery_id}/retry", response_model=WebhookDeliveryOut)
def retry_delivery(user: CurrentUser, delivery_id: int, db: DB):
    d = db.get(WebhookDelivery, delivery_id)
    if not d:
        raise HTTPException(status_code=404, detail="Delivery not found")
    d.status = "pending"
    d.attempts = 0
    db.commit()
    db.refresh(d)
    return d


@app.get("/health")
def health_check():
    return {"status": "ok", "service": "event-api"}
