"""
cloudx Dashboard — FastAPI reverse proxy + React static files server.

All /api/* requests are proxied to the appropriate backend service.
Auth token is passed through from the client's Authorization header.
WebSocket /ws/events broadcasts live status events from RabbitMQ.
"""
import asyncio
import fcntl
import json
import logging
import os
import pty
import struct
import sys
import termios
import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pymemcache.client.base import Client as MemcacheClient

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
MEMCACHED_URL      = os.getenv("MEMCACHED_URL",      "memcached:11211")
SESSION_COOKIE_NAME = "cloudx_session"

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
    allow_credentials=True,
)

_http_client: httpx.AsyncClient | None = None
_memcached: MemcacheClient | None = None

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


# ── XShell: WebSocket → PTY gateway ────────────────────────────────────────────
# Spawns a bash shell with the `cloudx` CLI pre-configured for the dashboard
# session's user/project, then pipes terminal bytes over the WebSocket. Pure
# stdlib (pty/termios/fcntl) — no extra dependencies.

async def _pty_read_loop(ws: WebSocket, master_fd: int) -> None:
    """Background task: read PTY output and stream it to the WebSocket."""
    loop = asyncio.get_event_loop()
    try:
        while True:
            try:
                data = await loop.run_in_executor(None, os.read, master_fd, 4096)
            except OSError:
                break
            if not data:
                break
            await ws.send_bytes(data)
    except Exception:
        pass


def _set_winsize(master_fd: int, cols: int, rows: int) -> None:
    try:
        fcntl.ioctl(master_fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    except Exception:
        pass


@app.websocket("/ws/xshell")
async def ws_xshell(ws: WebSocket):
    """XShell terminal: authenticate via the dashboard session cookie, spawn bash."""
    await ws.accept()

    session_id = ws.cookies.get(SESSION_COOKIE_NAME)
    if not session_id or not _memcached:
        await ws.send_text("\r\n✗ Not authenticated. Re-open the dashboard session.\r\n")
        await ws.close(code=4401)
        return

    raw = _memcached.get(f"session:{session_id}")
    if not raw:
        await ws.send_text("\r\n✗ Session expired — sign in again.\r\n")
        await ws.close(code=4401)
        return

    session = json.loads(raw.decode("utf-8"))
    user = session.get("user") or {}
    project = session.get("project") or {}
    token = session.get("token") or ""

    username = user.get("username", "user")
    project_name = project.get("name") or user.get("project_id", "")
    env_map = {
        "CLOUDX_AUTH_URL":       "http://127.0.0.1:8005",
        "CLOUDX_COMPUTE_URL":    "http://127.0.0.1:8003",
        "CLOUDX_NETWORK_URL":    "http://127.0.0.1:8001",
        "CLOUDX_IMAGE_URL":      "http://127.0.0.1:8002",
        "CLOUDX_VOLUME_URL":     "http://127.0.0.1:8004",
        "CLOUDX_PLACEMENT_URL":  "http://127.0.0.1:8006",
        "CLOUDX_TOKEN":          token,
        "CLOUDX_USER":           username,
        "CLOUDX_PROJECT":        project_name,
        "PS1":                   r"\u@cloudx-xshell:\[\e[32m\]\w\[\e[0m\]\$ ",
        "TERM":                  "xterm-256color",
    }

    # Spawn the shell in a fresh PTY.
    pid, master_fd = pty.fork()
    if pid == 0:  # child — exec the shell
        try:
            for k, v in env_map.items():
                os.environ[k] = v
            os.environ["HOME"] = os.getenv("HOME", "/root")
            os.chdir(os.environ["HOME"])
            os.execvp("bash", ["bash", "--noprofile", "--norc"])
        except Exception:
            os._exit(1)
    parent_pid = pid  # keep reference

    # Reader task drains PTY → WebSocket.
    reader = asyncio.create_task(_pty_read_loop(ws, master_fd))

    banner = (
        f"\r\n  Welcome to XShell (CloudX cloud shell)\r\n"
        f"  Authenticated as '{username}' (Project: {project_name})\r\n"
        f"  `cloudx` CLI is ready — try `cloudx server list`.\r\n"
        f"  Ctrl+D to exit.\r\n\r\n"
    )
    await ws.send_text(banner)

    try:
        while True:
            try:
                msg = await ws.receive_text()
            except WebSocketDisconnect:
                break
            # Handle resize control messages (JSON {"type":"resize",cols,rows}).
            if msg.startswith("{") and '"type"' in msg:
                try:
                    ctl = json.loads(msg)
                    if ctl.get("type") == "resize":
                        _set_winsize(master_fd, int(ctl.get("cols", 80)), int(ctl.get("rows", 24)))
                except Exception:
                    pass
                continue
            try:
                os.write(master_fd, msg.encode())
            except OSError:
                break
    finally:
        reader.cancel()
        # Reap the child and tidy up the PTY.
        try:
            os.close(master_fd)
        except Exception:
            pass
        try:
            os.kill(parent_pid, 15)
        except Exception:
            pass
        try:
            os.waitpid(parent_pid, 0)
        except Exception:
            pass
        log.info("XShell session ended for %s", username)


# ── Lifecycle ─────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    global _http_client, _event_consumer, _event_loop, _memcached
    _http_client = httpx.AsyncClient(timeout=60.0, follow_redirects=True)
    _event_loop = asyncio.get_event_loop()

    host, port = MEMCACHED_URL.split(":") if ":" in MEMCACHED_URL else (MEMCACHED_URL, 11211)
    _memcached = MemcacheClient((host, int(port)))
    try:
        _memcached.version()
        log.info("Memcached connected at %s", MEMCACHED_URL)
    except Exception as exc:
        log.warning("Could not connect to Memcached at %s: %s", MEMCACHED_URL, exc)

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
    if _memcached:
        _memcached.close()

# ── Session Endpoints ─────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str

@app.post("/api/auth/session/login")
async def session_login(payload: LoginRequest, response: Response):
    """Authenticate with auth-api, store token in Memcached, return HTTPOnly cookie."""
    try:
        resp = await _http_client.post(
            f"{AUTH_API_URL}/auth/tokens",
            json={"username": payload.username, "password": payload.password}
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"auth-api unreachable: {exc}")
    
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.json().get("detail", "Login failed"))
    
    data = resp.json()
    token = data.get("token")
    if not token:
        raise HTTPException(status_code=500, detail="No token returned from auth-api")
    
    # Store token and user data in Memcached (TTL: 24h = 86400s)
    session_id = str(uuid.uuid4())
    session_data = {
        "token": token,
        "user": data.get("user"),
        "project": data.get("project")
    }
    if _memcached:
        _memcached.set(f"session:{session_id}", json.dumps(session_data).encode("utf-8"), expire=86400)
    
    # Set cookie
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_id,
        httponly=True,
        max_age=86400,
        samesite="lax",
    )
    return {"message": "ok"}

@app.post("/api/auth/session/logout")
async def session_logout(request: Request, response: Response):
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if session_id and _memcached:
        _memcached.delete(f"session:{session_id}")
    response.delete_cookie(SESSION_COOKIE_NAME)
    return {"message": "ok"}

@app.get("/api/auth/session/me")
async def session_me(request: Request):
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if not session_id or not _memcached:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    raw = _memcached.get(f"session:{session_id}")
    if not raw:
        raise HTTPException(status_code=401, detail="Session expired")
    
    data = json.loads(raw.decode("utf-8"))
    
    # Extract fields cleanly for frontend context
    user = data.get("user") or {}
    project = data.get("project") or {}
    
    return {
        "username": user.get("username", ""),
        "user_id": user.get("id", ""),
        "role": user.get("role", "member"),
        "project_id": project.get("id") or user.get("project_id", ""),
        "project_name": project.get("name", ""),
    }

# ── Reverse Proxy ─────────────────────────────────────────────────────────────


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
    for hdr in ("x-auth-token", "content-type", "accept", "x-access-key-id", "x-secret-access-key"):
        val = request.headers.get(hdr)
        if val:
            fwd_headers[hdr] = val

    # Inject token from Memcached if available
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if session_id and _memcached:
        raw = _memcached.get(f"session:{session_id}")
        if raw:
            try:
                data = json.loads(raw.decode("utf-8"))
                if data.get("token"):
                    fwd_headers["authorization"] = f"Bearer {data['token']}"
            except Exception:
                pass
    
    # Fallback if authorization header was provided directly (e.g. CLI or tests)
    if not fwd_headers.get("authorization") and request.headers.get("authorization"):
        fwd_headers["authorization"] = request.headers.get("authorization")

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
