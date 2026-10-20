from __future__ import annotations
import os
from shared.config import load_service_config
load_service_config("ha-api")

"""cloudx ha API (Masakari-like): heartbeat host monitor, iSCSI fencing, evacuate of protected servers."""

import logging
import threading
import time
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import DateTime, Integer, String, create_engine, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from shared.auth_middleware import AdminUser, CurrentUser, InternalAuth
from shared.cors import cors_origins
from shared.domain import can_see, owns, visible_projects
from shared.request_id_middleware import RequestIDMiddleware
from shared.schema import require_schema

from . import monitor

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://cloudx:cloudx@127.0.0.1:5432/cloudx")
log = logging.getLogger("ha-api")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)

class Base(DeclarativeBase):
    pass

def new_id() -> str:
    return str(uuid.uuid4())

class Host(Base):
    __tablename__ = "ha_hosts"
    host: Mapped[str] = mapped_column(String, primary_key=True)
    initiator_iqn: Mapped[str | None] = mapped_column(String, nullable=True)
    last_seen: Mapped[datetime] = mapped_column(DateTime)
    state: Mapped[str] = mapped_column(String, default="up")  # up | suspect | recovering | fenced
    fails: Mapped[int] = mapped_column(Integer, default=0)
    suspect_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    watchdog_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

class Protected(Base):
    __tablename__ = "ha_protected"
    server_id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(String, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class Recovery(Base):
    __tablename__ = "ha_recoveries"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    host: Mapped[str] = mapped_column(String)
    server_id: Mapped[str | None] = mapped_column(String, nullable=True)
    project_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String)  # pending | running | done | failed | skipped
    message: Mapped[str | None] = mapped_column(String, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

app = FastAPI(title="cloudx ha-api", version="1.0.0")
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

def make_api():  # tests replace this with a fake
    return monitor.Api()

LOCK_KEY = 80200001  # pg advisory lock: whoever holds it runs the monitor
_lock_conn = None

def is_leader() -> bool:
    """True while this process holds the monitor lock; takes it over when free.

    Session-level lock on a dedicated connection, so it vanishes with the process or connection: no lease to renew.
    ponytail: SQLite (tests, single instance) is always leader; a half-open PG connection frees the lock only after the server notices.
    """
    global _lock_conn
    if engine.dialect.name != "postgresql":
        return True
    if _lock_conn is not None:
        try:
            _lock_conn.execute(text("SELECT 1"))
            return True
        except Exception:  # best-effort: a dead lock connection means the lock is gone; fall through to retake it
            log.warning("ha leader lock lost")
            _lock_conn.close()
            _lock_conn = None
    conn = engine.connect()
    if not conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY}).scalar():
        conn.close()
        return False
    _lock_conn = conn
    log.warning("ha leader lock taken: this instance now runs the monitor")
    with SessionLocal() as db:  # hosts were judged by the previous leader; give each a fresh 60 s
        for h in db.scalars(select(Host)):
            h.last_seen = datetime.utcnow()
        db.commit()
    return True

def start_monitor():  # tests replace this with a no-op
    def loop():
        while True:
            time.sleep(monitor.POLL)
            try:
                if not is_leader():
                    continue
                with SessionLocal() as db:
                    monitor.cycle(db, make_api(), datetime.utcnow())
            except Exception:  # best-effort: one bad cycle must not kill the monitor thread
                log.exception("ha monitor cycle failed")
    threading.Thread(target=loop, daemon=True).start()

@app.on_event("startup")
def startup():
    require_schema(engine, "ha")
    start_monitor()

@app.get("/health")
def health():
    return {"status": "ok"}

def _pid(current: dict) -> str:
    return current.get("project_id") or current.get("tenant_id") or "default"

def _iso(d):
    return d.isoformat() if d else None

@app.post("/internal/heartbeat/{host}", dependencies=[InternalAuth])
def heartbeat(host: str, body: dict, db: DB):
    iqn = body.get("initiator_iqn")
    h = db.get(Host, host)
    if not h:
        h = Host(host=host, last_seen=datetime.utcnow(), state="up", fails=0)
        db.add(h)
    h.last_seen = datetime.utcnow()
    if iqn:
        h.initiator_iqn = iqn
    db.commit()
    return {"ok": True}

@app.get("/internal/protected-domains/{host}", dependencies=[InternalAuth])
def protected_domains(host: str, db: DB):
    prot = set(db.scalars(select(Protected.server_id)))
    return {"domains": [s["libvirt_domain_name"] for s in make_api().servers()
                        if s["host"] == host and s["id"] in prot and s["status"] != "DELETED" and s.get("libvirt_domain_name")]}

@app.get("/internal/hosts/{host}", dependencies=[InternalAuth])
def internal_host(host: str, db: DB):
    h = db.get(Host, host)
    if not h:
        raise HTTPException(404, "Host not found")
    return {"state": h.state}

@app.post("/internal/watchdog/{host}", dependencies=[InternalAuth])
def watchdog_seen(host: str, db: DB):
    h = db.get(Host, host)
    if not h:
        raise HTTPException(404, "Host not found")
    h.watchdog_at = datetime.utcnow()
    db.commit()
    return {"ok": True}

@app.get("/hosts")
def list_hosts(current: CurrentUser, db: DB):
    return {"items": [{"host": h.host, "state": h.state, "last_seen": _iso(h.last_seen), "fails": h.fails,
                       "has_iqn": bool(h.initiator_iqn)} for h in db.scalars(select(Host).order_by(Host.host))]}

@app.post("/hosts/{host}/reset")
def reset_host(host: str, _: AdminUser, db: DB):
    h = db.get(Host, host)
    if not h:
        raise HTTPException(404, "Host not found")
    h.state, h.fails, h.suspect_at, h.last_seen = "up", 0, None, datetime.utcnow()
    db.commit()
    return {"host": h.host, "state": h.state}

def _prot(p: Protected) -> dict:
    return {"server_id": p.server_id, "project_id": p.project_id, "created_at": _iso(p.created_at)}

@app.get("/protected")
def list_protected(current: CurrentUser, db: DB):
    q = select(Protected).order_by(Protected.created_at)
    ids = visible_projects(current)
    if ids is not None:
        q = q.where(Protected.project_id.in_(ids or [_pid(current)]))
    return {"items": [_prot(p) for p in db.scalars(q)]}

@app.put("/protected/{server_id}")
def protect(server_id: str, current: CurrentUser, db: DB):
    api = make_api()
    try:
        srv = api.server(server_id)
        vols = api.server_volume_hosts(server_id)
    except monitor.Failure:
        raise HTTPException(404, "Server not found") from None
    if not can_see(current, srv.get("project_id")):
        raise HTTPException(404, "Server not found")
    if not owns(current, srv.get("project_id")):
        raise HTTPException(403, "Not allowed")
    if not vols or None in vols:
        raise HTTPException(422, "server volumes could not be located; only servers with a known root volume can be protected")
    if srv.get("host") in vols:
        raise HTTPException(422, "a volume lives on the same host as the server; it would be lost with that host")
    p = db.get(Protected, server_id)
    if not p:
        p = Protected(server_id=server_id, project_id=srv["project_id"])
        db.add(p)
        db.commit()
    return _prot(p)

@app.delete("/protected/{server_id}", status_code=204)
def unprotect(server_id: str, current: CurrentUser, db: DB):
    p = db.get(Protected, server_id)
    if not p or not can_see(current, p.project_id):
        raise HTTPException(404, "Server is not protected")
    if not owns(current, p.project_id):
        raise HTTPException(403, "Not allowed")
    db.delete(p)
    db.commit()

@app.get("/recoveries")
def list_recoveries(current: CurrentUser, db: DB):
    q = select(Recovery).order_by(Recovery.started_at.desc()).limit(200)
    ids = visible_projects(current)
    if ids is not None:
        q = q.where(Recovery.project_id.in_(ids or [_pid(current)]))
    return {"items": [{"id": r.id, "host": r.host, "server_id": r.server_id, "status": r.status, "message": r.message,
                       "started_at": _iso(r.started_at), "finished_at": _iso(r.finished_at)} for r in db.scalars(q)]}
