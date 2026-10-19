import requests
from fastapi import HTTPException
from shared.rpc import RPCClient, host_queue
from sqlalchemy import text

from .config import log
from .db import SessionLocal

# ── RPC Client (initialized on startup) ───────────────────────────────────────
_rpc: RPCClient | None = None
# ── HTTP helpers ───────────────────────────────────────────────────────────────

def _response_detail(response: requests.Response) -> str:
    try:
        return response.json().get("detail", response.text)
    except ValueError:
        return response.text


def get_server(ref: str) -> dict:
    """Resolve a server (id or name) for attach/detach flows.

    Queries the shared PostgreSQL compute_servers table directly instead of
    calling compute-api over HTTP — that endpoint requires an auth token this
    service-to-service call never had (the same silent-401 class of bug as the
    port-cleanup one). All CloudX services share one database.
    """
    with SessionLocal() as db:
        row = db.execute(
            text("SELECT id, name, status FROM compute_servers WHERE (id = :ref OR name = :ref) AND status != 'DELETED' LIMIT 1"),
            {"ref": ref},
        ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail=f"server '{ref}' not found")
    return dict(row)


# ── RPC helpers ────────────────────────────────────────────────────────────────

def worker_host(payload: dict) -> str | None:
    """Storage node that owns the volume/snapshot a message is about (None = any worker)."""
    from .models import Snapshot, Volume

    if payload.get("host"):
        return payload["host"]
    with SessionLocal() as db:
        volume_id = payload.get("volume_id")
        if not volume_id and payload.get("snapshot_id"):
            snapshot = db.get(Snapshot, payload["snapshot_id"])
            volume_id = snapshot.volume_id if snapshot else None
        volume = db.get(Volume, volume_id) if volume_id else None
    return volume.host if volume else None


def cast_to_worker(method: str, payload: dict) -> None:
    """Fire-and-forget to the block-store-worker that owns the volume (any worker if it has no host)."""
    global _rpc
    if _rpc is None:
        try:
            from .config import RABBITMQ_URL
            _rpc = RPCClient(RABBITMQ_URL)
        except Exception:  # best-effort: RPC broker unavailable, cast is dropped
            pass
    host = worker_host(payload)
    if _rpc is not None:
        _rpc.cast(host_queue("block-store", host), method, payload)
    log.info("CAST block-store.%s for %s (host=%s)", method,
             payload.get("volume_id") or payload.get("snapshot_id", "?"), host or "any")


def call_worker(host: str | None, method: str, payload: dict, timeout: int = 30) -> dict:
    """Synchronous RPC to the block-store-worker on *host*; raises when the broker is missing or the worker answers with an error."""
    if _rpc is None:
        raise RuntimeError("RPC broker not connected")
    reply = _rpc.call(host_queue("block-store", host), method, payload, timeout=timeout)
    if reply.get("error"):
        raise RuntimeError(reply["error"])
    return reply

def start_rpc(url: str) -> None:
    global _rpc
    _rpc = RPCClient(url)
