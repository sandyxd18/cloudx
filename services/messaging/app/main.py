from __future__ import annotations
import os
from shared.config import load_service_config
load_service_config("messaging-api")

"""cloudx messaging API (Zaqar-like): per-project queues with post / claim / delete, at-least-once, stored in PostgreSQL."""

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint, create_engine, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from shared.auth_middleware import CurrentUser
from shared.cors import cors_origins
from shared.domain import owns, visible_projects
from shared.request_id_middleware import RequestIDMiddleware
from shared.schema import require_schema

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://cloudx:cloudx@127.0.0.1:5432/cloudx")
MAX_BODY = 64 * 1024
MAX_ACTIVE = 10000
NAME_RE = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)

class Base(DeclarativeBase):
    pass

def new_id() -> str:
    return str(uuid.uuid4())

def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)

class Queue(Base):
    __tablename__ = "msg_queues"
    __table_args__ = (UniqueConstraint("project_id", "name"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(String, index=True)
    name: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

class Message(Base):
    __tablename__ = "msg_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)  # FIFO order
    queue_id: Mapped[str] = mapped_column(String, ForeignKey("msg_queues.id", ondelete="CASCADE"), index=True)
    body: Mapped[Any] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    claim_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    claimed_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deliveries: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

app = FastAPI(title="cloudx messaging-api", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=cors_origins(), allow_methods=["*"], allow_headers=["*"])
if RequestIDMiddleware:
    app.add_middleware(RequestIDMiddleware)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

DB = Annotated[Session, Depends(get_db)]

@app.on_event("startup")
def startup():
    require_schema(engine, "messaging")

@app.get("/health")
def health():
    return {"status": "ok"}

def _pid(current: dict) -> str:
    return current.get("project_id") or current.get("tenant_id") or "default"

def _queue(db: Session, name: str, current: dict, write: bool = False) -> Queue:
    if not NAME_RE.match(name):
        raise HTTPException(422, "Queue name: 1-64 letters, digits, '_' or '-'")
    q = db.scalar(select(Queue).where(Queue.project_id == _pid(current), Queue.name == name))
    if not q:
        raise HTTPException(404, "Queue not found")
    return q

def _live(q: Queue):
    return (Message.queue_id == q.id, Message.expires_at > now())

def _free(q: Queue):
    t = now()
    return (Message.queue_id == q.id, Message.expires_at > t, or_(Message.claim_id.is_(None), Message.claimed_until <= t))

def _msg(m: Message, claimed: bool | None = None) -> dict:
    t = now()
    return {"id": str(m.id), "body": m.body, "ttl": int((m.expires_at - t).total_seconds()), "age": int((t - m.created_at).total_seconds()),
            "deliveries": m.deliveries, "claim_id": m.claim_id if claimed else None}

@app.put("/queues/{name}", status_code=201)
def create_queue(name: str, current: CurrentUser, db: DB):
    if not NAME_RE.match(name):
        raise HTTPException(422, "Queue name: 1-64 letters, digits, '_' or '-'")
    if not owns(current, _pid(current)):
        raise HTTPException(403, "Not allowed")
    q = Queue(project_id=_pid(current), name=name)
    db.add(q)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Queue already exists") from None
    return {"id": q.id, "name": q.name, "project_id": q.project_id}

@app.get("/queues")
def list_queues(current: CurrentUser, db: DB):
    ids = visible_projects(current)
    sel = select(Queue).order_by(Queue.created_at)
    if ids is not None:
        sel = sel.where(Queue.project_id.in_(ids or [_pid(current)]))
    return {"items": [{"id": q.id, "name": q.name, "project_id": q.project_id} for q in db.scalars(sel)]}

@app.get("/queues/{name}/stats")
def stats(name: str, current: CurrentUser, db: DB):
    q = _queue(db, name, current)
    total = db.scalar(select(func.count()).where(*_live(q)))
    free = db.scalar(select(func.count()).where(*_free(q)))
    return {"total": total, "free": free, "claimed": total - free}

@app.delete("/queues/{name}", status_code=204)
def delete_queue(name: str, current: CurrentUser, db: DB):
    q = _queue(db, name, current)
    if not owns(current, q.project_id):
        raise HTTPException(403, "Not allowed")
    db.execute(delete(Message).where(Message.queue_id == q.id))
    db.delete(q)
    db.commit()

class MessageIn(BaseModel):
    body: Any
    ttl: int = Field(3600, ge=60, le=14 * 86400)

class PostIn(BaseModel):
    messages: list[MessageIn] = Field(min_length=1, max_length=10)

@app.post("/queues/{name}/messages", status_code=201)
def post_messages(name: str, data: PostIn, current: CurrentUser, db: DB):
    q = _queue(db, name, current)
    if not owns(current, q.project_id):
        raise HTTPException(403, "Not allowed")
    for m in data.messages:
        if len(json.dumps(m.body)) > MAX_BODY:
            raise HTTPException(413, "Message body is limited to 64 KiB")
    t = now()
    db.execute(delete(Message).where(Message.queue_id == q.id, Message.expires_at <= t))  # expired messages go on the next post
    if db.scalar(select(func.count()).where(Message.queue_id == q.id)) + len(data.messages) > MAX_ACTIVE:
        db.rollback()
        raise HTTPException(429, f"Queue is full ({MAX_ACTIVE} messages)")
    rows = [Message(queue_id=q.id, body=m.body, expires_at=t + timedelta(seconds=m.ttl), created_at=t) for m in data.messages]
    db.add_all(rows)
    db.commit()
    return {"ids": [str(r.id) for r in rows]}

@app.get("/queues/{name}/messages")
def peek(name: str, current: CurrentUser, db: DB, limit: int = 10):
    q = _queue(db, name, current)
    rows = db.scalars(select(Message).where(*_live(q)).order_by(Message.id).limit(max(1, min(limit, 50))))
    return {"items": [_msg(m) for m in rows]}

@app.post("/queues/{name}/claims")
def claim(name: str, current: CurrentUser, db: DB, ttl: int = 300, limit: int = 10):
    q = _queue(db, name, current)
    if not owns(current, q.project_id):
        raise HTTPException(403, "Not allowed")
    if not 30 <= ttl <= 43200:
        raise HTTPException(422, "ttl: 30 to 43200 seconds")
    t = now()
    rows = list(db.scalars(select(Message).where(*_free(q)).order_by(Message.id).limit(max(1, min(limit, 20)))
                           .with_for_update(skip_locked=True)))  # parallel workers never get the same message
    cid = new_id()
    for m in rows:
        m.claim_id, m.claimed_until, m.deliveries = cid, t + timedelta(seconds=ttl), m.deliveries + 1
    db.commit()
    return {"claim_id": cid if rows else None, "items": [_msg(m, True) for m in rows]}

@app.delete("/queues/{name}/messages/{mid}", status_code=204)
def delete_message(name: str, mid: int, current: CurrentUser, db: DB, claim_id: str | None = None):
    q = _queue(db, name, current)
    if not owns(current, q.project_id):
        raise HTTPException(403, "Not allowed")
    m = db.scalar(select(Message).where(Message.id == mid, Message.queue_id == q.id))
    if not m:
        raise HTTPException(404, "Message not found")
    if m.claim_id and m.claimed_until > now() and m.claim_id != claim_id:
        raise HTTPException(409, "Message is claimed; pass its claim_id")
    db.delete(m)
    db.commit()

@app.delete("/queues/{name}/claims/{claim_id}", status_code=204)
def release_claim(name: str, claim_id: str, current: CurrentUser, db: DB):
    q = _queue(db, name, current)
    if not owns(current, q.project_id):
        raise HTTPException(403, "Not allowed")
    rows = list(db.scalars(select(Message).where(Message.queue_id == q.id, Message.claim_id == claim_id)))
    if not rows:
        raise HTTPException(404, "Claim not found")
    for m in rows:
        m.claim_id = m.claimed_until = None
    db.commit()
