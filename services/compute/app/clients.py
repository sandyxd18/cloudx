import requests
from fastapi import HTTPException
from shared.rpc import RPCClient

from .config import (
    AUTH_ADMIN_PASSWORD,
    AUTH_ADMIN_USERNAME,
    AUTH_API_URL,
    BLOCK_STORE_API_URL,
    RPC_CALL_TIMEOUT,
    log,
)

# ── Internal Service Token ────────────────────────────────────────────────────
# JWT obtained at startup for service-to-service calls (networking, image, block-store).
_svc_token: str | None = None


def _get_svc_token() -> str | None:
    """Return a cached service token, refreshing if None."""
    global _svc_token
    if _svc_token:
        return _svc_token
    try:
        resp = requests.post(
            f"{AUTH_API_URL}/auth/tokens",
            json={"username": AUTH_ADMIN_USERNAME, "password": AUTH_ADMIN_PASSWORD},
            timeout=10,
        )
        if resp.status_code == 201:
            _svc_token = resp.json().get("token")
            log.info("compute-api: service token obtained")
        else:
            log.warning("compute-api: failed to obtain service token: HTTP %s", resp.status_code)
    except Exception as exc:
        log.warning("compute-api: could not reach auth-api for service token: %s", exc)
    return _svc_token


def _svc_headers() -> dict:
    token = _get_svc_token()
    if token:
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    return {"Content-Type": "application/json"}


# ── RPC Client (initialized on startup) ──────────────────────────────────────
_rpc: RPCClient | None = None


def api_get(url: str):
    try:
        response = requests.get(url, headers=_svc_headers(), timeout=8)
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if response.status_code == 401:
        # Token may have expired — clear and retry once with fresh token
        global _svc_token
        _svc_token = None
        fresh_headers = _svc_headers()
        try:
            response = requests.get(url, headers=fresh_headers, timeout=8)
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=response_detail(response))
    return response.json()


def api_post(url: str, payload: dict):
    try:
        response = requests.post(url, json=payload, headers=_svc_headers(), timeout=300)
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if response.status_code == 401:
        global _svc_token
        _svc_token = None
        try:
            response = requests.post(url, json=payload, headers=_svc_headers(), timeout=300)
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=response_detail(response))
    return response.json()


def api_delete(url: str, payload: dict | None = None):
    try:
        resp = requests.delete(url, json=payload, headers=_svc_headers(), timeout=20)
        if resp.status_code == 401:
            global _svc_token
            _svc_token = None
            requests.delete(url, json=payload, headers=_svc_headers(), timeout=20)
    except requests.RequestException:
        pass


def response_detail(response: requests.Response):
    try:
        return response.json().get("detail", response.text)
    except ValueError:
        return response.text


# ── RPC helpers (replaces worker_post / worker_delete) ────────────────────────

def cast_to_worker(method: str, payload: dict) -> None:
    """Fire-and-forget to compute worker via RabbitMQ (async)."""
    _rpc.cast("compute", method, payload)
    log.info("CAST compute.%s for server %s", method, payload.get("id", "?"))


def call_worker(method: str, payload: dict, timeout: int | None = None) -> dict:
    """Synchronous RPC call — waits for worker reply."""
    return _rpc.call("compute", method, payload, timeout=timeout or RPC_CALL_TIMEOUT)


def release_server_volumes(server_id: str) -> dict | None:
    try:
        response = requests.post(f"{BLOCK_STORE_API_URL}/servers/{server_id}/volumes/detach-all", timeout=20)
    except requests.RequestException:
        return None
    if response.status_code >= 400:
        return None
    return response.json()


def block_get(path: str) -> dict:
    return api_get(f"{BLOCK_STORE_API_URL}{path}")


def block_post(path: str, payload: dict) -> dict:
    return api_post(f"{BLOCK_STORE_API_URL}{path}", payload)


def block_delete(path: str) -> None:
    api_delete(f"{BLOCK_STORE_API_URL}{path}")


def start_rpc() -> None:
    global _rpc
    _rpc = RPCClient()
    log.info("RPC client initialized")


def stop_rpc() -> None:
    if _rpc:
        _rpc.close()
