import time

from fastapi import APIRouter, HTTPException
from shared.auth_middleware import CurrentUser

from .. import clients
from ..allocation import ALLOCATION_HISTORY_SAMPLE_INTERVAL, _allocation_history
from ..config import log
from ..db import DB
from ..service import placement_inventory

router = APIRouter()


@router.get("/host/check")
def host_check(_: CurrentUser):
    try:
        return clients.call_worker("health", {}, timeout=5)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Worker unreachable: {exc}") from exc


@router.get("/host")
def host_show(_: CurrentUser, db: DB):
    try:
        worker = clients.call_worker("health", {}, timeout=5)
    except Exception as exc:
        log.warning("compute worker health check failed: %s", exc)
        worker = {"status": "unreachable"}
    return {"host": "single-node", "worker": worker, "placement": placement_inventory(db)}


@router.get("/allocation-history")
def allocation_history(_: CurrentUser, hours: float = 24):
    """Cluster-wide vCPU/RAM/disk allocation trend for the dashboard Overview page.

    Backed by an in-memory ring buffer sampled every ALLOCATION_HISTORY_SAMPLE_INTERVAL
    seconds (default 60s); resets on API restart and only covers up to
    ALLOCATION_HISTORY_RETENTION_HOURS (default 24h) of history.
    """
    since = time.time() - max(0.0, hours) * 3600
    samples = [s for s in _allocation_history if s["timestamp"] >= since]
    return {"samples": samples, "sample_interval": ALLOCATION_HISTORY_SAMPLE_INTERVAL}
