from __future__ import annotations
import os
from shared.config import load_service_config
load_service_config("event-api")

"""
cloudx Event & Webhook API — Central Event Bus & Notification Engine
===================================================================

Service Phase 5.3: menangkap event audit/system dan mengirim webhook.
"""


import ipaddress
import json
import logging
import socket
from datetime import datetime, timezone
from typing import Annotated
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator
from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Integer, String, Text,
    create_engine, or_, select
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from shared.auth_middleware import AdminUser, CurrentUser, OptionalAuth
from shared.schema import require_schema

from shared.request_id_middleware import RequestIDMiddleware

log = logging.getLogger("event-api")

DATABASE_URL = os.getenv("EVENT_DATABASE_URL", os.getenv("DATABASE_URL", "postgresql+psycopg://cloudx:cloudx@127.0.0.1:5432/cloudx_events"))

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


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
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    subscription: Mapped["WebhookSubscription"] = relationship("WebhookSubscription", back_populates="deliveries")
    event: Mapped["EventLog"] = relationship("EventLog", lazy="joined")

    @property
    def event_type(self) -> str:
        return self.event.event_type

    @property
    def payload(self) -> str | None:
        return self.event.payload

    @property
    def attempt_count(self) -> int:
        return self.attempts


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


DB = Annotated[Session, Depends(get_db)]


def init_db():
    require_schema(engine, "event")


app = FastAPI(
    title="CloudX Event API",
    description="Central Event Bus & Webhook Notification Engine for CloudX",
    version="5.3.0",
)

if RequestIDMiddleware:
    app.add_middleware(RequestIDMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost").split(",") if o.strip() and o.strip() != "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ALLOW_PRIVATE = os.getenv("EVENT_ALLOW_PRIVATE_WEBHOOKS", "").lower() == "true"


def _is_admin(user: dict) -> bool:
    return user.get("role") == "admin"


def _check_url(url: str) -> None:
    """SSRF guard: reject hosts resolving to loopback/private/link-local/etc."""
    host = urlparse(url).hostname
    if not host:
        raise ValueError("URL has no host")
    if ALLOW_PRIVATE:
        return
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise ValueError("URL host cannot be resolved")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise ValueError("URL host resolves to a non-public address")


def persist_event(db: Session, event_type: str, resource_type: str, resource_id: str | None,
                  project_id: str | None, user_id: str | None, payload: str | None) -> EventLog:
    entry = EventLog(event_type=event_type, resource_type=resource_type, resource_id=resource_id,
                     project_id=project_id, user_id=user_id, payload=payload)
    db.add(entry)
    db.flush()
    if project_id:
        subs = db.execute(select(WebhookSubscription).where(
            WebhookSubscription.is_active == True, WebhookSubscription.project_id == project_id,
        )).scalars().all()
        for sub in subs:
            types = [t.strip() for t in sub.event_types.split(",")]
            if "*" in types or event_type in types:
                db.add(WebhookDelivery(subscription_id=sub.id, event_id=entry.id, status="pending"))
    db.commit()
    db.refresh(entry)
    return entry


def _on_bus_event(msg: dict) -> None:
    try:
        etype = msg.get("type")
        if not etype:
            return
        p = msg.get("payload")
        p = p if isinstance(p, dict) else {}
        rid = p.get("server_id") or p.get("volume_id") or p.get("id")
        with SessionLocal() as db:
            persist_event(db, etype, etype.split(".")[0], str(rid) if rid is not None else None,
                          p.get("project_id"), p.get("user_id"), json.dumps(p, default=str))
    except Exception:
        log.exception("failed to persist bus event")


@app.on_event("startup")
def on_startup():
    init_db()
    try:
        from shared.rpc import EventConsumer
        EventConsumer(_on_bus_event, os.getenv("RABBITMQ_URL")).start()  # spawns its own daemon thread
    except Exception:
        log.exception("event consumer not started")
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
        _check_url(v)
        return v


class WebhookSubscriptionOut(BaseModel):
    id: int
    project_id: str
    name: str
    endpoint_url: str
    event_types: str
    has_secret: bool
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class WebhookSubscriptionCreated(WebhookSubscriptionOut):
    secret: str | None


def _sub_out(sub: WebhookSubscription, model=WebhookSubscriptionOut):
    d = {k: getattr(sub, k) for k in ("id", "project_id", "name", "endpoint_url", "event_types", "is_active", "created_at")}
    d["has_secret"] = bool(sub.secret)
    if model is WebhookSubscriptionCreated:
        d["secret"] = sub.secret
    return model(**d)


class DeliveryPatch(BaseModel):
    status: str | None = None
    response_code: int | None = None
    attempts: int | None = None
    next_retry_at: datetime | None = None
    last_error: str | None = None


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
    attempt_count: int
    event_type: str
    payload: str | None
    next_retry_at: datetime | None
    last_error: str | None
    created_at: datetime

    class Config:
        from_attributes = True


# ── API Endpoints ─────────────────────────────────────────────────────────────

@app.post("/events/emit", response_model=EventLogOut, status_code=status.HTTP_201_CREATED)
def emit_event(user: CurrentUser, data: EventEmitIn, db: DB):
    payload_str = json.dumps(data.payload) if isinstance(data.payload, dict) else data.payload
    pid = user.get("project_id")
    if _is_admin(user) and data.project_id:
        pid = data.project_id
    uid = user.get("sub") or "system"
    if _is_admin(user) and data.user_id:
        uid = data.user_id
    return persist_event(db, data.event_type, data.resource_type, data.resource_id, pid, uid, payload_str)


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
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    q = select(EventLog)
    if _is_admin(user):
        if project_id:
            q = q.where(EventLog.project_id == project_id)
    else:
        q = q.where(EventLog.project_id == user.get("project_id"))
    if since:
        q = q.where(EventLog.timestamp >= since)
    if until:
        q = q.where(EventLog.timestamp <= until)
    if event_type:
        q = q.where(EventLog.event_type == event_type)
    if resource_type:
        q = q.where(EventLog.resource_type == resource_type)
    if resource_id:
        q = q.where(EventLog.resource_id == resource_id)
    q = q.order_by(EventLog.timestamp.desc(), EventLog.id.desc()).limit(limit).offset(offset)
    return db.execute(q).scalars().all()


def _own_sub(user: dict, db: Session, sub_id: int) -> WebhookSubscription:
    sub = db.get(WebhookSubscription, sub_id)
    if not sub or (not _is_admin(user) and sub.project_id != user.get("project_id")):
        raise HTTPException(status_code=404, detail="Subscription not found")
    return sub


@app.post("/events/subscriptions", response_model=WebhookSubscriptionCreated, status_code=status.HTTP_201_CREATED)
def create_subscription(user: CurrentUser, data: WebhookSubscriptionIn, db: DB):
    pid = user.get("project_id")
    if not pid:
        raise HTTPException(status_code=400, detail="token has no project_id")
    event_types_str = ",".join(data.event_types)
    sub = WebhookSubscription(
        project_id=pid,
        name=data.name,
        endpoint_url=data.endpoint_url,
        event_types=event_types_str,
        secret=data.secret,
        is_active=data.is_active,
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return _sub_out(sub, WebhookSubscriptionCreated)


@app.get("/events/subscriptions", response_model=list[WebhookSubscriptionOut])
def list_subscriptions(user: CurrentUser, db: DB, project_id: str | None = None,
                       limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
    pid = project_id if _is_admin(user) and project_id else user.get("project_id")
    q = select(WebhookSubscription).where(WebhookSubscription.project_id == pid)
    q = q.order_by(WebhookSubscription.id).limit(limit).offset(offset)
    return [_sub_out(s) for s in db.execute(q).scalars().all()]


@app.get("/events/subscriptions/{sub_id}", response_model=WebhookSubscriptionOut)
def get_subscription(user: CurrentUser, sub_id: int, db: DB):
    return _sub_out(_own_sub(user, db, sub_id))


@app.get("/events/internal/subscriptions/{sub_id}", response_model=WebhookSubscriptionCreated)
def internal_get_subscription(user: AdminUser, sub_id: int, db: DB):
    return _sub_out(_own_sub(user, db, sub_id), WebhookSubscriptionCreated)


@app.delete("/events/subscriptions/{sub_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_subscription(user: CurrentUser, sub_id: int, db: DB):
    db.delete(_own_sub(user, db, sub_id))
    db.commit()
    return None


def _own_delivery(user: dict, db: Session, delivery_id: int) -> WebhookDelivery:
    d = db.get(WebhookDelivery, delivery_id)
    if not d or (not _is_admin(user) and d.subscription.project_id != user.get("project_id")):
        raise HTTPException(status_code=404, detail="Delivery not found")
    return d


@app.get("/events/deliveries", response_model=list[WebhookDeliveryOut])
def list_deliveries(user: CurrentUser, db: DB, subscription_id: int | None = None, status: str | None = None,
                    limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
    q = select(WebhookDelivery)
    if not _is_admin(user):
        q = q.join(WebhookSubscription, WebhookDelivery.subscription_id == WebhookSubscription.id).where(
            WebhookSubscription.project_id == user.get("project_id"))
    if subscription_id:
        q = q.where(WebhookDelivery.subscription_id == subscription_id)
    if status:
        q = q.where(WebhookDelivery.status == status)
        if status == "pending":
            q = q.where(or_(WebhookDelivery.next_retry_at.is_(None),
                            WebhookDelivery.next_retry_at <= datetime.now(timezone.utc)))
    q = q.order_by(WebhookDelivery.created_at.desc(), WebhookDelivery.id.desc()).limit(limit).offset(offset)
    return db.execute(q).scalars().unique().all()


@app.patch("/events/deliveries/{delivery_id}", response_model=WebhookDeliveryOut)
def patch_delivery(user: AdminUser, delivery_id: int, data: DeliveryPatch, db: DB):
    d = _own_delivery(user, db, delivery_id)
    if data.status is not None and data.status not in ("pending", "success", "failed"):
        raise HTTPException(status_code=422, detail="status must be pending, success or failed")
    for k in data.model_fields_set:
        setattr(d, k, getattr(data, k))
    db.commit()
    db.refresh(d)
    return d


@app.post("/events/deliveries/{delivery_id}/retry", response_model=WebhookDeliveryOut)
def retry_delivery(user: CurrentUser, delivery_id: int, db: DB):
    d = _own_delivery(user, db, delivery_id)
    d.status = "pending"
    d.attempts = 0
    d.next_retry_at = None
    db.commit()
    db.refresh(d)
    return d


@app.get("/health")
def health_check():
    return {"status": "ok", "service": "event-api"}
