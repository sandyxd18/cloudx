import requests
from fastapi import HTTPException
from shared.rpc import RPCClient
from sqlalchemy import text

from shared.service_client import ServiceClient

from .config import log
from .db import SessionLocal

# Service-to-service HTTP client (caches the admin service token for calls to auth-api).
service_client = ServiceClient(service_name="service_block_store")

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

def cast_to_worker(method: str, payload: dict) -> None:
    """Fire-and-forget to block-store-worker via RabbitMQ."""
    _rpc.cast("block-store", method, payload)
    log.info("CAST block-store.%s for %s", method, payload.get("volume_id") or payload.get("snapshot_id", "?"))


def start_rpc(url: str) -> None:
    global _rpc
    _rpc = RPCClient(url)
