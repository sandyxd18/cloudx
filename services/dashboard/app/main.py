"""
cloudx Dashboard — FastAPI reverse proxy + React static files server.

All /api/* requests are proxied to the appropriate backend service.
Auth token is passed through from the client's Authorization header.
WebSocket /ws/events broadcasts live status events from RabbitMQ.
"""
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

import httpx
from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# Import shared RPC EventConsumer
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
try:
    from shared.rpc import EventConsumer
except ImportError:
    EventConsumer = None  # graceful fallback if RabbitMQ not available

log = logging.getLogger("dashboard")

AUTH_API_URL       = os.getenv("AUTH_API_URL",       "http://auth-api:8000").rstrip("/")
COMPUTE_API_URL    = os.getenv("COMPUTE_API_URL",    "http://compute-api:8000").rstrip("/")
NETWORKING_API_URL = os.getenv("NETWORKING_API_URL", "http://networking-api:8000").rstrip("/")
IMAGE_API_URL      = os.getenv("IMAGE_API_URL",      "http://image-api:8000").rstrip("/")
BLOCK_STORE_API_URL= os.getenv("BLOCK_STORE_API_URL","http://block-store-api:8000").rstrip("/")
PLACEMENT_API_URL  = os.getenv("PLACEMENT_API_URL",  "http://placement-api:8000").rstrip("/")
RABBITMQ_URL       = os.getenv("RABBITMQ_URL",       "amqp://cloudx:cloudx@rabbitmq:5672/")

# Route prefix → backend URL
_ROUTES: list[tuple[str, str]] = [
    ("/api/auth",        AUTH_API_URL),
    ("/api/compute",     COMPUTE_API_URL),
    ("/api/networking",  NETWORKING_API_URL),
    ("/api/image",       IMAGE_API_URL),
    ("/api/block-store", BLOCK_STORE_API_URL),
    ("/api/placement",   PLACEMENT_API_URL),
]

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="cloudx dashboard", version="1.0.0", docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_http_client: httpx.AsyncClient | None = None

# ── WebSocket live events ─────────────────────────────────────────────────────

_ws_clients: set[WebSocket] = set()
_event_consumer: EventConsumer | None = None
_event_loop: asyncio.AbstractEventLoop | None = None


def _on_amqp_event(msg: dict) -> None:
    """Called from EventConsumer background thread when an event arrives."""
    if not _ws_clients or not _event_loop:
        return
    payload = json.dumps(msg)
    # Schedule broadcast on the main asyncio loop
    asyncio.run_coroutine_threadsafe(_broadcast(payload), _event_loop)


async def _broadcast(payload: str) -> None:
    """Send event to all connected WebSocket clients."""
    dead = set()
    for ws in _ws_clients:
        try:
            await ws.send_text(payload)
        except Exception:
            dead.add(ws)
    _ws_clients.difference_update(dead)


@app.websocket("/ws/events")
async def ws_events(ws: WebSocket):
    await ws.accept()
    _ws_clients.add(ws)
    log.info("WebSocket client connected (%d total)", len(_ws_clients))
    try:
        while True:
            # Keep connection alive — client sends pings or we just wait
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _ws_clients.discard(ws)
        log.info("WebSocket client disconnected (%d remaining)", len(_ws_clients))


# ── Lifecycle ─────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    global _http_client, _event_consumer, _event_loop
    _http_client = httpx.AsyncClient(timeout=60.0, follow_redirects=True)
    _event_loop = asyncio.get_event_loop()

    # Start AMQP event consumer in background thread
    if EventConsumer:
        try:
            _event_consumer = EventConsumer(_on_amqp_event, RABBITMQ_URL)
            _event_consumer.start()
            log.info("AMQP event consumer started")
        except Exception as exc:
            log.warning("Could not start AMQP event consumer: %s", exc)


@app.on_event("shutdown")
async def shutdown():
    if _http_client:
        await _http_client.aclose()
    if _event_consumer:
        _event_consumer.stop()


def _resolve_backend(path: str) -> tuple[str, str] | None:
    for prefix, backend in _ROUTES:
        if path.startswith(prefix):
            return backend, path[len(prefix):]
    return None


@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def proxy(path: str, request: Request):
    full_path = f"/api/{path}"
    resolved = _resolve_backend(full_path)
    if resolved is None:
        return Response(content="unknown api prefix", status_code=404)
    backend_url, backend_path = resolved
    if not backend_path.startswith("/"):
        backend_path = f"/{backend_path}"

    # Forward query string
    qs = str(request.url.query)
    target = f"{backend_url}{backend_path}"
    if qs:
        target = f"{target}?{qs}"

    # Forward relevant headers (auth + content-type)
    fwd_headers = {}
    for hdr in ("authorization", "x-auth-token", "content-type", "accept"):
        val = request.headers.get(hdr)
        if val:
            fwd_headers[hdr] = val

    body = await request.body()

    resp = await _http_client.request(
        method=request.method,
        url=target,
        headers=fwd_headers,
        content=body if body else None,
    )

    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=dict(resp.headers),
        media_type=resp.headers.get("content-type"),
    )


# ── Serve React SPA ───────────────────────────────────────────────────────────

if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

@app.get("/{full_path:path}", include_in_schema=False)
async def spa_fallback(full_path: str):
    """Serve index.html for all non-API routes (SPA client-side routing)."""
    index = STATIC_DIR / "index.html"
    if not index.exists():
        return Response(content="Dashboard not built yet", status_code=503)
    return FileResponse(index)
