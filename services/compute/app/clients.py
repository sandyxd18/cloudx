import requests
from fastapi import HTTPException
from shared.catalog import CatalogError
from shared.internal import internal_headers
from shared.rpc import RPCClient, host_queue

from .config import (
    block_store_api_url,
    RPC_CALL_TIMEOUT,
    log,
)

# ── RPC Client (initialized on startup) ──────────────────────────────────────
_rpc: RPCClient | None = None


def api_get(url: str):
    try:
        response = requests.get(url, headers={"Content-Type": "application/json", **internal_headers()}, timeout=8)
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=response_detail(response))
    return response.json()


def api_post(url: str, payload: dict):
    try:
        response = requests.post(url, json=payload, headers={"Content-Type": "application/json", **internal_headers()}, timeout=300)
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=response_detail(response))
    return response.json()


def api_delete(url: str, payload: dict | None = None):
    try:
        requests.delete(url, json=payload, headers={"Content-Type": "application/json", **internal_headers()}, timeout=20)
    except requests.RequestException:
        pass


def response_detail(response: requests.Response):
    try:
        return response.json().get("detail", response.text)
    except ValueError:
        return response.text


# ── RPC helpers (replaces worker_post / worker_delete) ────────────────────────

LEGACY_HOST = "single-node"  # what workers recorded as server.host before multi-node: no queue of its own


def worker_queue(payload: dict) -> str:
    """Queue of the compute node that runs the server (the shared queue when the host is unknown)."""
    host = payload.get("host")
    return host_queue("compute", None if host == LEGACY_HOST else host)


def cast_to_worker(method: str, payload: dict) -> None:
    """Fire-and-forget to the compute worker of the server's host via RabbitMQ (async)."""
    _rpc.cast(worker_queue(payload), method, payload)
    log.info("CAST %s.%s for server %s", worker_queue(payload), method, payload.get("id", "?"))


def call_worker(method: str, payload: dict, timeout: int | None = None) -> dict:
    """Synchronous RPC call — waits for worker reply."""
    return _rpc.call(worker_queue(payload), method, payload, timeout=timeout or RPC_CALL_TIMEOUT)


def release_server_volumes(server_id: str) -> dict | None:
    try:
        response = requests.post(f"{block_store_api_url()}/servers/{server_id}/volumes/detach-all", headers=internal_headers(), timeout=20)
    except (requests.RequestException, CatalogError):
        return None
    if response.status_code >= 400:
        return None
    return response.json()


def block_get(path: str) -> dict:
    return api_get(f"{block_store_api_url()}{path}")


def block_post(path: str, payload: dict) -> dict:
    return api_post(f"{block_store_api_url()}{path}", payload)


def block_delete(path: str) -> None:
    api_delete(f"{block_store_api_url()}{path}")


def start_rpc() -> None:
    global _rpc
    _rpc = RPCClient()
    log.info("RPC client initialized")


def stop_rpc() -> None:
    if _rpc:
        _rpc.close()
