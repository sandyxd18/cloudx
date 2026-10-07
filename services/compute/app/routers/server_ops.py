import time

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from shared.auth_middleware import CurrentUser

from .. import clients
from ..config import (
    BLOCK_STORE_API_URL,
    DEFAULT_VM_USER,
    IMAGE_API_URL,
    NETWORKING_API_URL,
    NOVNC_BASE_URL,
    ONLY_BACKEND,
    RPC_CALL_TIMEOUT,
    log,
)
from ..db import DB
from ..lookups import require_server
from ..service import server_worker_payload

router = APIRouter()


@router.get("/servers/{ref}/console")
def console(ref: str, current: CurrentUser, db: DB):
    server = require_server(db, ref, current)
    return {
        "server": server.name,
        "domain": server.libvirt_domain_name,
        "domain_name": server.libvirt_domain_name,
        "instruction": f"virsh -c qemu:///system console {server.libvirt_domain_name}",
        "note": "VNC is auto-assigned by libvirt; use virsh dumpxml to inspect the assigned port if needed.",
    }


@router.get("/servers/{ref}/console-url")
def console_url(ref: str, current: CurrentUser, db: DB):
    server = require_server(db, ref, current)
    vnc = {
        "status": "auto-assigned-by-libvirt",
        "inspect": f"virsh -c qemu:///system dumpxml {server.libvirt_domain_name}",
    }
    try:
        vnc = clients.call_worker("get_console_info", server_worker_payload(server), timeout=RPC_CALL_TIMEOUT)
    except Exception as exc:
        log.warning("could not get console info from the compute worker: %s", exc)
    novnc_url = NOVNC_BASE_URL
    if vnc.get("port"):
        novnc_url = f"{NOVNC_BASE_URL}?host=localhost&port={vnc['port']}"
    return {
        "server": server.name,
        "domain": server.libvirt_domain_name,
        "virsh_console": f"virsh -c qemu:///system console {server.libvirt_domain_name}",
        "vnc": vnc,
        "novnc_url": novnc_url,
        "note": "noVNC static UI is exposed on port 6080; VNC port is discovered from libvirt when available.",
    }


@router.post("/servers/{ref}/remote-consoles")
def remote_console(ref: str, payload: dict, current: CurrentUser, db: DB):
    """
    OpenStack-compatible remote console endpoint.
    Returns a noVNC URL for in-browser VM console access.

    Request body: {"remote_console": {"type": "novnc", "protocol": "vnc"}}
    """
    server = require_server(db, ref, current)
    if server.status != "ACTIVE":
        raise HTTPException(status_code=409, detail=f"server must be ACTIVE to get a console (current: {server.status})")

    vnc_info: dict = {}
    try:
        vnc_info = clients.call_worker("get_console_info", server_worker_payload(server), timeout=RPC_CALL_TIMEOUT)
    except Exception as exc:
        log.warning("could not get console info from the compute worker: %s", exc)

    novnc_url = NOVNC_BASE_URL
    if vnc_info.get("port"):
        novnc_url = f"{NOVNC_BASE_URL}?host=localhost&port={vnc_info['port']}"

    return {
        "remote_console": {
            "type": "novnc",
            "protocol": "vnc",
            "url": novnc_url,
            "vnc_port": vnc_info.get("port"),
            "server": server.name,
            "websocket_url": f"/servers/{ref}/console/ws",
        }
    }


@router.websocket("/servers/{ref}/console/ws")
async def console_websocket(websocket: WebSocket, ref: str):
    await websocket.accept()
    await websocket.send_text(f"\r\n--- Connected to CloudX Serial Console for server {ref} ---\r\n")
    try:
        while True:
            data = await websocket.receive_text()
            if data:
                await websocket.send_text(f"echo: {data}")
    except WebSocketDisconnect:
        pass
    except Exception:  # best-effort: client disconnected
        pass


@router.post("/servers/{ref}/shelve")
def shelve_server(ref: str, current: CurrentUser, db: DB):
    """
    Shelve a server: snapshot its state to an image, then stop and free resources.
    Status: ACTIVE/SHUTOFF → SHELVED_OFFLOADED
    """
    server = require_server(db, ref, current)
    if server.locked and current.get("role") != "admin":
        raise HTTPException(status_code=409, detail="server is locked")
    if server.status not in ("ACTIVE", "SHUTOFF", "PAUSED", "SUSPENDED"):
        raise HTTPException(status_code=409, detail=f"cannot shelve server in state {server.status}")
    if not server.root_volume_id:
        raise HTTPException(status_code=409, detail="shelve requires a root volume (boot-from-volume)")

    server.status = "SHELVING"
    db.commit()
    clients.cast_to_worker("shelve", {
        **server_worker_payload(server),
        "_image_api_url": IMAGE_API_URL,
        "_block_store_url": BLOCK_STORE_API_URL,
    })
    return {"server": server.name, "status": "shelving"}


@router.post("/servers/{ref}/unshelve")
def unshelve_server(ref: str, current: CurrentUser, db: DB):
    """
    Unshelve a server: restore from shelved image and restart.
    Status: SHELVED_OFFLOADED → ACTIVE
    """
    server = require_server(db, ref, current)
    if server.locked and current.get("role") != "admin":
        raise HTTPException(status_code=409, detail="server is locked")
    if server.status != "SHELVED_OFFLOADED":
        raise HTTPException(status_code=409, detail=f"server must be SHELVED_OFFLOADED to unshelve (current: {server.status})")

    server.status = "UNSHELVING"
    db.commit()
    clients.cast_to_worker("unshelve", {
        **server_worker_payload(server),
        "_image_api_url": IMAGE_API_URL,
        "_block_store_url": BLOCK_STORE_API_URL,
        "_networking_url": NETWORKING_API_URL,
    })
    return {"server": server.name, "status": "unshelving"}


_METRICS_TIME_RANGE_HOURS = {"1h": 1, "3h": 3, "6h": 6, "12h": 12, "24h": 24}


@router.get("/servers/{ref}/metrics")
def server_metrics(ref: str, current: CurrentUser, db: DB, time_range: str = "1h"):
    """EC2-style instance monitoring: CPU/memory/network/disk samples from the hypervisor.

    Backed by compute-worker's in-memory ring buffer (see collect_metrics_loop);
    samples are taken every 10s and retained for up to 24h, reset on worker restart.
    """
    server = require_server(db, ref, current)
    hours = _METRICS_TIME_RANGE_HOURS.get(time_range, 1)
    since_epoch = time.time() - hours * 3600
    try:
        result = clients.call_worker("get_metrics", {"server_id": server.id, "host": server.host, "since_epoch": since_epoch}, timeout=RPC_CALL_TIMEOUT)
        result["time_range"] = time_range
        return result
    except Exception as exc:
        return {"server_id": server.id, "samples": [], "sample_interval": 10, "time_range": time_range, "error": str(exc)}


@router.get("/servers/{ref}/logs")
def logs(ref: str, current: CurrentUser, db: DB, lines: int = 50):
    server = require_server(db, ref, current)
    try:
        result = clients.call_worker("get_logs", {**server_worker_payload(server), "lines": lines}, timeout=RPC_CALL_TIMEOUT)
        return result
    except Exception as exc:
        log.warning("could not get logs from the compute worker: %s", exc)
        return {
            "server": server.name,
            "domain": server.libvirt_domain_name,
            "output": "",
            "message": "console log streaming not available; use virsh console or check host libvirt logs",
        }


@router.get("/servers/{ref}/ssh-info")
def ssh_info(ref: str, current: CurrentUser, db: DB):
    server = require_server(db, ref, current)
    command = None
    if server.ip_address and server.keypair_name:
        command = f"ssh -i <private-key-for-{server.keypair_name}> {DEFAULT_VM_USER}@{server.ip_address}"
    return {
        "server": server.name,
        "backend": ONLY_BACKEND,
        "keypair_name": server.keypair_name,
        "ip_address": server.ip_address,
        "default_username": DEFAULT_VM_USER,
        "ssh_command": command,
    }
