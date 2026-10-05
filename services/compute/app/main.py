from shared.config import load_service_config

load_service_config("compute-api")

import threading

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from shared.request_id_middleware import RequestIDMiddleware
from shared.schema import require_schema
from sqlalchemy import text

from . import clients
from .allocation import allocation_history_loop
from .bootstrap import bootstrap_flavors
from .config import ONLY_BACKEND, log
from .db import DB, engine
from .routers import (
    aggregates,
    discovery,
    flavors,
    host,
    keypairs,
    server_actions,
    server_groups,
    server_meta,
    server_ops,
    servers,
)

app = FastAPI(title="cloudx compute-api", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


if RequestIDMiddleware:
    app.add_middleware(RequestIDMiddleware)


try:
    from shared.metrics import setup_metrics
    setup_metrics(app, "compute-api")
except Exception as exc:
    log.warning("metrics endpoint disabled: %s", exc)


@app.on_event("startup")
def startup():
    require_schema(engine, "compute")
    bootstrap_flavors()


@app.on_event("startup")
def _startup_rpc():
    clients.start_rpc()
    threading.Thread(target=allocation_history_loop, daemon=True).start()


@app.on_event("shutdown")
def _shutdown_rpc():
    clients.stop_rpc()


@app.get("/health")
def health():
    return {"status": "ok", "service": "compute-api", "default_backend": ONLY_BACKEND}


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


app.include_router(host.router)
app.include_router(flavors.router)
app.include_router(server_groups.router)
app.include_router(servers.router)
app.include_router(server_actions.router)
app.include_router(server_ops.router)
app.include_router(keypairs.router)
app.include_router(server_meta.router)
app.include_router(discovery.router)
app.include_router(aggregates.router)
