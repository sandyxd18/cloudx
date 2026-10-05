import os
import time
from collections import deque

from .config import log
from .db import SessionLocal
from .service import placement_inventory

# Cluster-wide allocation trend for the dashboard Overview page: sample
# placement_inventory() every ALLOCATION_HISTORY_SAMPLE_INTERVAL seconds, keep
# ALLOCATION_HISTORY_RETENTION_HOURS worth of points in memory.
# ponytail: in-memory ring buffer, lost on API restart. Same upgrade path as
# compute-worker's instance metrics — a dedicated monitoring service/timeseries
# DB if/when that's built.
ALLOCATION_HISTORY_SAMPLE_INTERVAL = int(os.getenv("ALLOCATION_HISTORY_SAMPLE_INTERVAL", "60"))
ALLOCATION_HISTORY_RETENTION_HOURS = int(os.getenv("ALLOCATION_HISTORY_RETENTION_HOURS", "24"))
_ALLOCATION_HISTORY_SIZE = max(1, (ALLOCATION_HISTORY_RETENTION_HOURS * 3600) // ALLOCATION_HISTORY_SAMPLE_INTERVAL)
_allocation_history: deque = deque(maxlen=_ALLOCATION_HISTORY_SIZE)


def collect_allocation_history() -> None:
    """One sampling pass: snapshot cluster-wide vCPU/RAM/disk allocation for the Overview trend chart."""
    with SessionLocal() as db:
        try:
            inv = placement_inventory(db)
        except Exception as exc:
            log.warning("[allocation-history] sampling failed: %s", exc)
            return
    _allocation_history.append({"timestamp": time.time(), **inv})


def allocation_history_loop() -> None:
    log.info("[allocation-history] cluster trend sampler started (interval=%ds, retention=%dh)",
              ALLOCATION_HISTORY_SAMPLE_INTERVAL, ALLOCATION_HISTORY_RETENTION_HOURS)
    while True:
        time.sleep(ALLOCATION_HISTORY_SAMPLE_INTERVAL)
        collect_allocation_history()
