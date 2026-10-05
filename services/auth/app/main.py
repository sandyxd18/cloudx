from shared.config import load_service_config

load_service_config("auth-api")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from shared.request_id_middleware import RequestIDMiddleware
from sqlalchemy import text

from .bootstrap import _bootstrap
from .config import log
from .db import DB
from .routers import (
    access_keys,
    app_credentials,
    audit,
    catalog,
    discovery,
    domains,
    groups,
    policy,
    projects,
    tokens,
    users,
)

# ── App ────────────────────────────────────────────────────────────────────────
app = FastAPI(title="cloudx auth-api", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


if RequestIDMiddleware:
    app.add_middleware(RequestIDMiddleware)


# Prometheus metrics (/metrics) — optional, no-op if prometheus_client missing
try:
    from shared.metrics import setup_metrics
    setup_metrics(app, "auth-api")
except Exception as exc:
    log.warning("metrics endpoint disabled: %s", exc)


@app.on_event("startup")
def startup():
    _bootstrap()


@app.get("/health")
def health():
    return {"status": "ok", "service": "auth-api"}


@app.get("/healthcheck")
def healthcheck(db: DB):
    """Standard CloudX health check endpoint."""
    checks = {}
    overall = "up"
    try:
        db.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as exc:
        checks["db"] = f"error: {exc}"
        overall = "degraded"
    return {"status": overall, "checks": checks}


app.include_router(tokens.router)
app.include_router(projects.router)
app.include_router(users.router)
app.include_router(groups.router)
app.include_router(access_keys.router)
app.include_router(app_credentials.router)
app.include_router(catalog.router)
app.include_router(domains.router)
app.include_router(policy.router)
app.include_router(audit.router)
app.include_router(discovery.router)
