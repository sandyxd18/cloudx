from shared.config import load_service_config

load_service_config("networking-api")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from shared.request_id_middleware import RequestIDMiddleware
from shared.cors import cors_origins
from shared.schema import require_schema
from sqlalchemy import text

from .bootstrap import bootstrap_default_security_group
from .config import log
from .db import DB, engine
from .routers import (
    discovery,
    floating_ips,
    l3,
    networks,
    port_forwarding,
    ports,
    qos,
    routing,
    security_groups,
    subnet_pools,
    subnets,
    trunks,
    vpn,
)

app = FastAPI(title="cloudx networking-api", version="1.0.0")

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
    setup_metrics(app, "networking-api")
except Exception as exc:
    log.warning("metrics endpoint disabled: %s", exc)

@app.on_event("startup")
def startup():
    require_schema(engine, "networking")
    try:
        bootstrap_default_security_group()
    except Exception as exc:
        print(f"default security group bootstrap skipped: {exc}", flush=True)

@app.get("/health")
def health():
    return {"status": "ok", "service": "networking-api"}

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


app.include_router(networks.router)
app.include_router(subnets.router)
app.include_router(l3.router)
app.include_router(routing.router)
app.include_router(vpn.router)
app.include_router(ports.router)
app.include_router(floating_ips.router)
app.include_router(security_groups.router)
app.include_router(qos.router)
app.include_router(subnet_pools.router)
app.include_router(port_forwarding.router)
app.include_router(discovery.router)
app.include_router(trunks.router)
