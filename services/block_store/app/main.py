from shared.config import load_service_config

load_service_config("block-store-api")

import threading

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from shared.request_id_middleware import RequestIDMiddleware
from shared.cors import cors_origins
from shared.schema import require_schema
from sqlalchemy import text

from . import clients
from .bootstrap import _bootstrap_quotas
from .config import (
    CEPH_POOL,
    LVM_VG_NAME,
    RABBITMQ_URL,
    STORAGE_CEPH_RBD_DRIVER,
    STORAGE_LVM_DRIVER,
    log,
)
from .db import DB, engine
from .routers import (
    backups,
    consistency_groups,
    discovery,
    quotas,
    replication,
    schedules,
    snapshots,
    volume_actions,
    volume_types,
    volumes,
)
from .schedules import _snapshot_schedule_loop

"""
cloudx block-store-api (refactored — stateless, no privileged)
================================================================

Handles CRUD, status management, and volume type management.
All storage backend operations (LVM/Ceph) are delegated to block-store-worker
via RabbitMQ (AMQP) RPC.

Queue used:
  Cast → 'block-store'   for async ops (create, delete, extend, snapshot)
  Call → 'block-store'   for sync ops (attach/detach via compute-worker)

Status transitions:
  create_volume  → status='creating'  → worker → 'available' (or 'error')
  delete_volume  → status='deleting'  → worker → (record deleted)
  extend_volume  → status='extending' → worker → 'available'
  snapshot       → status='creating'  → worker → 'available'
"""
app = FastAPI(title="cloudx block-store-api", version="3.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
)


if RequestIDMiddleware:
    app.add_middleware(RequestIDMiddleware)


try:
    from shared.metrics import setup_metrics
    setup_metrics(app, "block-store-api")
except Exception as exc:
    log.warning("metrics endpoint disabled: %s", exc)


@app.on_event("startup")
def startup():
    if not STORAGE_LVM_DRIVER and not STORAGE_CEPH_RBD_DRIVER:
        raise RuntimeError(
            "No storage driver is enabled! "
            "Set STORAGE_LVM_DRIVER=true and/or STORAGE_CEPH_RBD_DRIVER=true in .env"
        )
    require_schema(engine, "block_store")
    _bootstrap_quotas()
    clients.start_rpc(RABBITMQ_URL)
    threading.Thread(target=_snapshot_schedule_loop, daemon=True).start()
    log.info("Block-store-api ready (RabbitMQ RPC mode)")
    log.info("Drivers: lvm=%s ceph=%s", STORAGE_LVM_DRIVER, STORAGE_CEPH_RBD_DRIVER)


@app.on_event("shutdown")
def shutdown():
    if clients._rpc:
        clients._rpc.close()


# ── Health ─────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "block-store-api",
        "mode": "rpc",
        "drivers": {
            "lvm":  STORAGE_LVM_DRIVER,
            "ceph": STORAGE_CEPH_RBD_DRIVER,
        },
        "lvm_vg":    LVM_VG_NAME if STORAGE_LVM_DRIVER else None,
        "ceph_pool": CEPH_POOL if STORAGE_CEPH_RBD_DRIVER else None,
    }


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


app.include_router(volumes.router)
app.include_router(schedules.router)
app.include_router(snapshots.router)
app.include_router(backups.router)
app.include_router(volume_types.router)
app.include_router(quotas.router)
app.include_router(consistency_groups.router)
app.include_router(volume_actions.router)
app.include_router(discovery.router)
app.include_router(replication.router)
