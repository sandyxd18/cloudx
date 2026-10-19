"""
cloudx Dashboard — FastAPI reverse proxy + React static files server.

All /api/* requests are proxied to the appropriate backend service.
Auth token is passed through from the client's Authorization header.
WebSocket /ws/events broadcasts live status events from RabbitMQ.
"""
from shared.config import load_service_config
load_service_config("dashboard")

import asyncio
import datetime
import fcntl
import json
import logging
import os
import pty
import shutil
import struct
import termios
import threading
import uuid
from pathlib import Path


import httpx
import websockets
from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pymemcache.client.base import PooledClient as MemcacheClient

# Import shared RPC EventConsumer
from shared.rpc import EventConsumer
from shared.cors import cors_origins

log = logging.getLogger("dashboard")

AUTH_API_URL       = os.getenv("AUTH_API_URL",       "http://auth-api:8000").rstrip("/")
COMPUTE_API_URL    = os.getenv("COMPUTE_API_URL",    "http://compute-api:8000").rstrip("/")
NETWORKING_API_URL = os.getenv("NETWORKING_API_URL", "http://networking-api:8000").rstrip("/")
IMAGE_API_URL      = os.getenv("IMAGE_API_URL",      "http://image-api:8000").rstrip("/")
BLOCK_STORE_API_URL= os.getenv("BLOCK_STORE_API_URL","http://block-store-api:8000").rstrip("/")
PLACEMENT_API_URL  = os.getenv("PLACEMENT_API_URL",  "http://placement-api:8000").rstrip("/")
OBJECT_STORE_API_URL = os.getenv("OBJECT_STORE_API_URL","http://object-store-api:8000").rstrip("/")
LOAD_BALANCER_API_URL = os.getenv("LOAD_BALANCER_API_URL","http://load-balancer-api:8000").rstrip("/")
DNS_API_URL = os.getenv("DNS_API_URL","http://dns-api:8016").rstrip("/")
FILE_STORE_API_URL = os.getenv("FILE_STORE_API_URL","http://file-store-api:8017").rstrip("/")
CONTAINER_API_URL = os.getenv("CONTAINER_API_URL","http://container-api:8018").rstrip("/")
DATABASE_API_URL = os.getenv("DATABASE_API_URL","http://database-api:8019").rstrip("/")
HA_API_URL = os.getenv("HA_API_URL","http://ha-api:8020").rstrip("/")
TELEMETRY_API_URL  = os.getenv("TELEMETRY_API_URL",  "http://telemetry-api:8000").rstrip("/")
BILLING_API_URL    = os.getenv("BILLING_API_URL",    "http://billing-api:8000").rstrip("/")
EVENT_API_URL      = os.getenv("EVENT_API_URL",      "http://event-api:8000").rstrip("/")
ORCHESTRATION_API_URL = os.getenv("ORCHESTRATION_API_URL","http://orchestration-api:8000").rstrip("/")
KEY_MANAGER_API_URL = os.getenv("KEY_MANAGER_API_URL","http://key-manager-api:8000").rstrip("/")
K8S_ENGINE_API_URL = os.getenv("K8S_ENGINE_API_URL", "http://k8s-engine-api:8000").rstrip("/")
RABBITMQ_URL       = os.getenv("RABBITMQ_URL",       "amqp://cloudx:cloudx@127.0.0.1:5672/")
MEMCACHED_URL      = os.getenv("MEMCACHED_URL",      "memcached:11211")
SESSION_COOKIE_NAME = "cloudx_session"

# Route prefix → backend URL
_ROUTES: list[tuple[str, str]] = [
    ("/api/auth",          AUTH_API_URL),
    ("/api/compute",       COMPUTE_API_URL),
    ("/api/networking",    NETWORKING_API_URL),
    ("/api/image",         IMAGE_API_URL),
    ("/api/block-store",   BLOCK_STORE_API_URL),
    ("/api/placement",     PLACEMENT_API_URL),
    ("/api/object-store",  OBJECT_STORE_API_URL),
    ("/api/load-balancer", LOAD_BALANCER_API_URL),
    ("/api/dns", DNS_API_URL),
    ("/api/file-store", FILE_STORE_API_URL),
    ("/api/container", CONTAINER_API_URL),
    ("/api/database", DATABASE_API_URL),
    ("/api/ha", HA_API_URL),
    ("/api/telemetry",     TELEMETRY_API_URL),
    ("/api/billing",       BILLING_API_URL),
    ("/api/event",         EVENT_API_URL),
    ("/api/orchestration", ORCHESTRATION_API_URL),
    ("/api/key-manager",   KEY_MANAGER_API_URL),
    ("/api/k8s-engine",    K8S_ENGINE_API_URL),
]

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="cloudx dashboard", version="1.0.0", docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
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
        except Exception:  # best-effort: a failed send marks the client as dead; it is dropped below
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

def _set_winsize(master_fd: int, cols: int, rows: int) -> None:
    try:
        fcntl.ioctl(master_fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    except (OSError, struct.error):  # closed fd, or a size outside 0..65535 sent by the browser
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

    # Allocate PTY pair.  We keep master in the server, pass slave_fd to the
    # child.  Using asyncio.subprocess (instead of pty.fork) keeps Uvicorn's
    # event loop healthy on Linux: no locks inherited from the parent process.
    master_fd, slave_fd = pty.openpty()
    os.set_blocking(master_fd, False)

    env = os.environ.copy()
    env.update(env_map)
    env["HOME"] = os.getenv("HOME", "/root")

    try:
        proc = await asyncio.create_subprocess_exec(
            "bash", "-i",
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            env=env,
            cwd=env["HOME"],
            preexec_fn=lambda: os.login_tty(slave_fd),
        )
    except Exception as exc:
        log.exception("XShell: failed to spawn bash")
        await ws.send_text(f"\r\n✗ Failed to spawn shell: {exc}\r\n")
        try:
            os.close(master_fd)
            os.close(slave_fd)
        except OSError:
            pass
        await ws.close(code=4500)
        return
    finally:
        # Slave is owned by the child now; close our copy.
        try:
            os.close(slave_fd)
        except OSError:
            pass

    loop = asyncio.get_event_loop()

    def _on_master_ready():
        try:
            data = os.read(master_fd, 4096)
            if data:
                asyncio.create_task(ws.send_bytes(data))
            else:
                loop.remove_reader(master_fd)
        except BlockingIOError:
            pass
        except OSError:
            try:
                loop.remove_reader(master_fd)
            except Exception:  # best-effort: reader already removed
                pass

    try:
        loop.add_reader(master_fd, _on_master_ready)
    except Exception:  # best-effort: event loop without reader support; the shell just stays unread
        pass

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
                except (ValueError, TypeError, AttributeError):  # not a control message
                    pass
                continue
            try:
                os.write(master_fd, msg.encode())
            except OSError:
                break
    finally:
        try:
            loop.remove_reader(master_fd)
        except Exception:  # best-effort: reader already removed
            pass
        try:
            os.close(master_fd)
        except OSError:
            pass
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            pass
        log.info("XShell session ended for %s", username)


# ── Lifecycle ─────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    global _http_client, _event_consumer, _event_loop, _memcached
    _http_client = httpx.AsyncClient(timeout=60.0, follow_redirects=True)
    _event_loop = asyncio.get_event_loop()

    host, port = MEMCACHED_URL.split(":") if ":" in MEMCACHED_URL else (MEMCACHED_URL, 11211)
    _memcached = MemcacheClient((host, int(port)), max_pool_size=25, timeout=2.0, connect_timeout=2.0, ignore_exc=True)
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

# ── Session Endpoints & Storage ───────────────────────────────────────────────

_local_sessions: dict[str, dict] = {}
_session_lock = threading.Lock()


def _save_session(session_id: str, session_data: dict) -> None:
    with _session_lock:
        _local_sessions[session_id] = session_data
    if _memcached:
        try:
            _memcached.set(f"session:{session_id}", json.dumps(session_data).encode("utf-8"), expire=86400)
        except Exception as exc:
            log.warning("Memcached set failed: %s", exc)


def _get_session(session_id: str) -> dict | None:
    if not session_id:
        return None
    with _session_lock:
        local = _local_sessions.get(session_id)
        if local:
            return local

    if _memcached:
        try:
            raw = _memcached.get(f"session:{session_id}")
            if raw:
                data = json.loads(raw.decode("utf-8"))
                with _session_lock:
                    _local_sessions[session_id] = data
                return data
        except Exception as exc:
            log.warning("Memcached get failed: %s", exc)

    with _session_lock:
        return _local_sessions.get(session_id)


def _delete_session(session_id: str) -> None:
    if not session_id:
        return
    with _session_lock:
        _local_sessions.pop(session_id, None)
    if _memcached:
        try:
            _memcached.delete(f"session:{session_id}")
        except Exception as exc:
            log.warning("Memcached delete failed: %s", exc)


class LoginRequest(BaseModel):
    username: str
    password: str
    project: str | None = None


@app.post("/api/auth/session/login")
async def session_login(payload: LoginRequest, response: Response):
    """Authenticate with auth-api, store token in session store, return HTTPOnly cookie."""
    auth_payload = {"username": payload.username, "password": payload.password}
    if payload.project:
        auth_payload["project"] = payload.project
    try:
        resp = await _http_client.post(
            f"{AUTH_API_URL}/auth/tokens",
            json=auth_payload,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"auth-api unreachable: {exc}")
    
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.json().get("detail", "Login failed"))
    
    data = resp.json()
    token = data.get("token")
    if not token:
        raise HTTPException(status_code=500, detail="No token returned from auth-api")
    
    # Store token and user data in dual-layer session store (TTL: 24h = 86400s)
    session_id = str(uuid.uuid4())
    session_data = {
        "token": token,
        "user": data.get("user"),
        "project": data.get("project")
    }
    _save_session(session_id, session_data)
    
    # Set cookie
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_id,
        httponly=True,
        max_age=86400,
        path="/",
        samesite="lax",
    )
    return {
        "message": "ok",
        "token": token,
        "user": data.get("user"),
        "project": data.get("project"),
    }


class SwitchProjectRequest(BaseModel):
    project_id: str


@app.post("/api/auth/session/switch-project")
async def session_switch_project(payload: SwitchProjectRequest, request: Request, response: Response):
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    session_data = _get_session(session_id) if session_id else {}
    token = session_data.get("token")
    if not token:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        resp = await _http_client.post(
            f"{AUTH_API_URL}/session/switch-project",
            json={"project_id": payload.project_id},
            headers={"authorization": f"Bearer {token}"},
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"auth-api unreachable: {exc}")

    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.json().get("detail", "Failed to switch project"))

    data = resp.json()
    new_token = data.get("token")
    if new_token:
        new_session_data = {
            "token": new_token,
            "user": data.get("user") or session_data.get("user"),
            "project": data.get("project") or session_data.get("project"),
        }
        if session_id:
            _save_session(session_id, new_session_data)
        else:
            session_id = str(uuid.uuid4())
            _save_session(session_id, new_session_data)
            response.set_cookie(
                key=SESSION_COOKIE_NAME,
                value=session_id,
                httponly=True,
                max_age=86400,
                path="/",
                samesite="lax",
            )
    return data


@app.post("/api/auth/session/logout")
async def session_logout(request: Request, response: Response):
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if session_id:
        _delete_session(session_id)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return {"message": "ok"}


@app.get("/api/auth/session/me")
async def session_me(request: Request):
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    data = _get_session(session_id) if session_id else None

    # Fallback to Authorization header if cookie is missing (e.g. Incognito mode blocking cookies)
    if not data:
        auth = request.headers.get("authorization")
        if auth and auth.lower().startswith("bearer "):
            token = auth.split(" ", 1)[1].strip()
            try:
                resp = await _http_client.get(
                    f"{AUTH_API_URL}/auth/tokens/validate",
                    headers={"authorization": f"Bearer {token}"}
                )
                if resp.status_code == 200:
                    val = resp.json()
                    return {
                        "username": val.get("username", ""),
                        "user_id": val.get("sub") or val.get("user_id", ""),
                        "role": val.get("role", "member"),
                        "domain_admin": bool(val.get("domain_admin")),
                        "project_id": val.get("project_id", ""),
                        "project_name": val.get("project_name", ""),
                    }
            except Exception as exc:
                log.warning("Token validation failed in session_me: %s", exc)

    if not data:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    # Extract fields cleanly for frontend context
    user = data.get("user") or {}
    project = data.get("project") or {}
    
    return {
        "username": user.get("username", ""),
        "user_id": user.get("id", ""),
        "role": user.get("role", "member"),
        "domain_admin": bool(user.get("is_domain_admin")),
        "project_id": project.get("id") or user.get("project_id", ""),
        "project_name": project.get("name", ""),
    }

# ── XIDE workspace management ─────────────────────────────────────────────────
# Lightweight workspace metadata stored under a JSON file on the shared volume.
# Each workspace is a subdirectory inside the code-server /home/coder tree.

XIDE_ROOT = Path(os.getenv("XIDE_WORKSPACE_ROOT", "/var/lib/xide-workspaces"))
XIDE_META_FILE = XIDE_ROOT / ".cloudx-xide-workspaces.json"


def _xide_guard(request: Request) -> dict | None:
    """Verify the dashboard session cookie and return session data or None."""
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    return _get_session(session_id)


def _xide_load_meta() -> list[dict]:
    _xide_ensure_dir()
    if XIDE_META_FILE.exists():
        try:
            return json.loads(XIDE_META_FILE.read_text())
        except (OSError, ValueError) as exc:
            log.warning("could not read workspace metadata, starting empty: %s", exc)
            return []
    return []


def _xide_save_meta(meta: list[dict]) -> None:
    _xide_ensure_dir()
    XIDE_META_FILE.write_text(json.dumps(meta, indent=2))


def _xide_ensure_dir() -> None:
    XIDE_ROOT.mkdir(parents=True, exist_ok=True)


@app.get("/api/xide/workspaces")
async def xide_list_workspaces(request: Request):
    """List all XIDE workspaces. Session auth required."""
    session = _xide_guard(request)
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = session.get("user") or {}
    username = user.get("username", "")
    all_meta = _xide_load_meta()
    items = [w for w in all_meta if w.get("owner") == username]
    return {"items": items}


@app.post("/api/xide/workspaces")
async def xide_create_workspace(request: Request):
    """Create a new XIDE workspace under /home/coder/<username>/<workspace-name>."""
    session = _xide_guard(request)
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")

    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Workspace name is required")
    description = (body.get("description") or "").strip()
    profile = (body.get("profile") or "default").strip()
    auto_stop = (body.get("auto_stop") or "1h").strip()

    user = session.get("user") or {}
    username = user.get("username", "user")
    owner_dir = XIDE_ROOT / username
    ws_dir = owner_dir / name

    all_meta = _xide_load_meta()
    if any(w.get("owner") == username and w.get("name") == name for w in all_meta):
        raise HTTPException(status_code=409, detail=f"Workspace '{name}' already exists")

    owner_dir.mkdir(parents=True, exist_ok=True)
    ws_dir.mkdir(parents=True, exist_ok=True)

    ws_id = str(uuid.uuid4())[:12]
    entry = {
        "id": ws_id,
        "name": name,
        "owner": username,
        "description": description,
        "profile": profile,
        "auto_stop": auto_stop,
        "path": str(ws_dir),
        "created_at": datetime.datetime.utcnow().isoformat() + "Z",
    }

    all_meta.append(entry)
    _xide_save_meta(all_meta)
    return {"workspace": entry}


@app.delete("/api/xide/workspaces/{ws_id}")
async def xide_delete_workspace(ws_id: str, request: Request):
    """Delete an XIDE workspace including its directory on disk."""
    session = _xide_guard(request)
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")

    user = session.get("user") or {}
    username = user.get("username", "")
    all_meta = _xide_load_meta()
    entry = next((w for w in all_meta if w.get("id") == ws_id and w.get("owner") == username), None)
    if not entry:
        raise HTTPException(status_code=404, detail="Workspace not found")

    ws_path = Path(entry.get("path", ""))
    if ws_path.exists():
        shutil.rmtree(ws_path, ignore_errors=True)

    all_meta = [w for w in all_meta if w.get("id") != ws_id]
    _xide_save_meta(all_meta)
    return {"message": "ok"}


# ── XIDE reverse proxy → code-server container ─────────────────────────────────

XIDE_BACKEND = os.getenv("XIDE_BACKEND_URL", "http://127.0.0.1:8085").rstrip("/")
XIDE_WS_BACKEND = XIDE_BACKEND.replace("http://", "ws://").replace("https://", "wss://")
XIDE_PROXY_PREFIX = "/xide-proxy"


async def _xide_http_proxy(request: Request) -> Response:
    """Forward HTTP requests to code-server, guarded by dashboard session auth."""
    session = _xide_guard(request)
    if session is None:
        return Response(content="Not authenticated", status_code=401)

    full_path = request.url.path
    rel_path = full_path[len(XIDE_PROXY_PREFIX):] if full_path.startswith(XIDE_PROXY_PREFIX) else full_path
    if not rel_path.startswith("/"):
        rel_path = f"/{rel_path}"
    qs = str(request.url.query)
    target = f"{XIDE_BACKEND}{rel_path}"
    if qs:
        target = f"{target}?{qs}"

    body = await request.body()

    fwd_headers = {}
    for hdr in ("content-type", "accept", "accept-language",
                "cache-control"):
        val = request.headers.get(hdr)
        if val:
            fwd_headers[hdr] = val

    resp = await _http_client.request(
        method=request.method,
        url=target,
        headers=fwd_headers,
        content=body if body else None,
    )

    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers={k: v for k, v in resp.headers.items()
                 if k.lower() not in ("transfer-encoding", "content-encoding")},
        media_type=resp.headers.get("content-type"),
    )


@app.api_route(f"{XIDE_PROXY_PREFIX}/{{path:path}}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
async def xide_proxy_route(path: str, request: Request):
    """Proxy all XIDE HTTP requests to code-server after session auth check.
    
    Uses request.url.path directly so the full /xide-proxy/... prefix reaches
    the upstream code-server (which expects it via --base-path).
    """
    return await _xide_http_proxy(request)


@app.get(XIDE_PROXY_PREFIX, include_in_schema=False)
@app.get(f"{XIDE_PROXY_PREFIX}/", include_in_schema=False)
async def xide_index_proxy(request: Request):
    """Proxy XIDE index (code-server entrypoint) with auth guard."""
    return await _xide_http_proxy(request)


@app.websocket(f"{XIDE_PROXY_PREFIX}/{{path:path}}")
async def ws_xide_proxy(ws: WebSocket, path: str = ""):
    """WebSocket proxy for code-server's real-time channel.

    Bridges the browser WebSocket through FastAPI → upstream code-server.
    Supports sub-paths (e.g. /socket) and forwards subprotocols + cookies.
    """
    session_id = ws.cookies.get(SESSION_COOKIE_NAME)
    if not session_id or not _memcached:
        await ws.accept()
        await ws.send_text("\r\n\x1b[31m✗ Not authenticated\x1b[0m\r\n")
        await ws.close(code=4401)
        return

    raw = _memcached.get(f"session:{session_id}")
    if not raw:
        await ws.accept()
        await ws.send_text("\r\n\x1b[31m✗ Session expired — sign in again\x1b[0m\r\n")
        await ws.close(code=4401)
        return

    # Accept with the same subprotocol(s) the browser requested
    subprotocols = ws.scope.get("subprotocols", [])
    await ws.accept(subprotocol=subprotocols[0] if subprotocols else None)

    # Build upstream URL preserving sub-path and query string
    upstream_url = f"{XIDE_WS_BACKEND}/{path}"
    if ws.url.query:
        upstream_url = f"{upstream_url}?{ws.url.query.decode()}"

    # Forward cookie so upstream code-server recognises the session
    extra_headers = {}
    cookie = ws.headers.get("cookie")
    if cookie:
        extra_headers["Cookie"] = cookie

    try:
        async with websockets.connect(
            upstream_url,
            subprotocols=subprotocols,
            extra_headers=extra_headers,
        ) as upstream:
            async def client_to_upstream():
                try:
                    while True:
                        data = await ws.receive()
                        if "text" in data:
                            await upstream.send(data["text"])
                        elif "bytes" in data:
                            await upstream.send(data["bytes"])
                except Exception:  # best-effort: either side closing the websocket ends the proxy
                    pass

            async def upstream_to_client():
                try:
                    async for message in upstream:
                        if isinstance(message, bytes):
                            await ws.send_bytes(message)
                        else:
                            await ws.send_text(message)
                except Exception:  # best-effort: either side closing the websocket ends the proxy
                    pass

            client_task = asyncio.create_task(client_to_upstream())
            upstream_task = asyncio.create_task(upstream_to_client())

            done, _ = await asyncio.wait(
                [client_task, upstream_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in [client_task, upstream_task]:
                t.cancel()
    except Exception:  # best-effort: either side closing the websocket ends the proxy
        pass


# ── Reverse Proxy ─────────────────────────────────────────────────────────────


def _resolve_backend(path: str) -> tuple[str, str] | None:
    for prefix, backend in _ROUTES:
        if path.startswith(prefix):
            rel = path[len(prefix):]
            if not rel.startswith("/"):
                rel = f"/{rel}"
            # Normalize paths for services whose backend routes have service-specific prefixes
            if prefix == "/api/telemetry" and not rel.startswith("/telemetry"):
                rel = f"/telemetry{rel}"
            elif prefix == "/api/billing" and not rel.startswith("/billing"):
                rel = f"/billing{rel}"
            elif prefix == "/api/event" and not rel.startswith("/events"):
                rel = f"/events{rel}"
            elif prefix == "/api/placement" and rel.startswith("/resource-providers"):
                rel = rel.replace("/resource-providers", "/resource_providers", 1)
            return backend, rel
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

    # Inject token from session store if available
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if session_id:
        data = _get_session(session_id)
        if data and data.get("token"):
            fwd_headers["authorization"] = f"Bearer {data['token']}"
    
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


@app.get("/storage/{bucket_name}", include_in_schema=False)
@app.get("/storage/{bucket_name}/", include_in_schema=False)
async def storage_gateway_list(bucket_name: str, request: Request):
    """List objects in a bucket via the storage gateway."""
    target = f"{OBJECT_STORE_API_URL}/buckets/{bucket_name}/objects"
    qs = str(request.url.query)
    if qs:
        target = f"{target}?{qs}"

    fwd_headers = {}
    client_ip = request.client.host if request.client else "127.0.0.1"
    fwd_headers["x-forwarded-for"] = client_ip

    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if session_id:
        data = _get_session(session_id)
        if data and data.get("token"):
            fwd_headers["x-auth-token"] = data["token"]

    for hdr in ("x-auth-token", "authorization"):
        val = request.headers.get(hdr)
        if val and hdr not in fwd_headers:
            fwd_headers[hdr] = val

    try:
        resp = await _http_client.get(target, headers=fwd_headers)
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            headers=dict(resp.headers),
            media_type=resp.headers.get("content-type"),
        )
    except Exception as exc:
        return Response(content=f"storage gateway error: {exc}", status_code=502)


@app.get("/storage/{bucket_name}/{key:path}", include_in_schema=False)
async def storage_gateway(bucket_name: str, key: str, request: Request):
    """Path-style gateway for object downloads: /storage/<bucket>/<key>.
    
    Forwards X-Forwarded-For so object-store-api can enforce network scope.
    Auth tokens from session cookie are forwarded for private bucket access.
    """
    target = f"{OBJECT_STORE_API_URL}/buckets/{bucket_name}/objects/{key}"
    qs = str(request.url.query)
    if qs:
        target = f"{target}?{qs}"

    fwd_headers = {}
    client_ip = request.client.host if request.client else "127.0.0.1"
    fwd_headers["x-forwarded-for"] = client_ip

    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if session_id:
        data = _get_session(session_id)
        if data and data.get("token"):
            fwd_headers["x-auth-token"] = data["token"]

    for hdr in ("x-auth-token", "authorization", "x-access-key-id", "x-secret-access-key"):
        val = request.headers.get(hdr)
        if val and hdr not in fwd_headers:
            fwd_headers[hdr] = val

    try:
        resp = await _http_client.get(target, headers=fwd_headers)
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            headers={k: v for k, v in resp.headers.items()
                     if k.lower() not in ("transfer-encoding",)},
            media_type=resp.headers.get("content-type"),
        )
    except Exception as exc:
        return Response(content=f"storage gateway error: {exc}", status_code=502)


# ── Serve React SPA ───────────────────────────────────────────────────────────

if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

# NOTE: /xide/* routes are defined above (before SPA fallback) so they take
# priority over the catch-all /{full_path:path} below.

@app.api_route("/downloads/{filename}", methods=["GET", "HEAD"], include_in_schema=False)
async def download_release(filename: str):
    """Serve release tarball packages for node deployments."""
    if not (filename.endswith(".tar.gz") or filename.endswith(".sha256")):
        return Response(content="Invalid file requested", status_code=400)

    possible_paths = [
        Path("/opt/cloudx/dist") / filename,
        Path(__file__).resolve().parents[3] / "dist" / filename,
        STATIC_DIR / "downloads" / filename,
    ]
    for p in possible_paths:
        if p.exists() and p.is_file():
            media_type = "application/gzip" if filename.endswith(".tar.gz") else "text/plain"
            return FileResponse(p, media_type=media_type, filename=filename)

    return Response(content=f"Release artifact {filename} not found", status_code=404)

@app.get("/{full_path:path}", include_in_schema=False)
async def spa_fallback(full_path: str, request: Request):
    """Serve index.html for all non-API routes (SPA client-side routing)."""
    if full_path.startswith("xide-proxy"):
        return Response(content="Not found", status_code=404)
    index = STATIC_DIR / "index.html"
    if not index.exists():
        return Response(content="Dashboard not built yet", status_code=503)
    return FileResponse(index, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
