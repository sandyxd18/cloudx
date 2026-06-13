from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from . import libvirt_driver, volume_manager, worker
from .config import LIBVIRT_URI, OVS_BRIDGE
from .utils import run, tool_available


class ServerAction(BaseModel):
    name: str | None = None
    image_id: str | None = None
    image: dict | None = None
    flavor: str | None = None
    port: dict | None = None
    keypair_name: str | None = None
    public_key_snapshot: str | None = None
    libvirt_domain_name: str | None = None
    instance_dir: str | None = None
    disk_path: str | None = None
    tap_name: str | None = None
    mac_address: str | None = None
    vcpus: int | None = None
    ram_mb: int | None = None
    disk_gb: int | None = None
    flavor_name: str | None = None
    flavor_snapshot: dict | None = None
    volume: dict | None = None
    reboot_type: str | None = "soft"
    new_vcpus: int | None = None
    new_ram_mb: int | None = None
    rescue_image_id: str | None = None
    password: str | None = None
    username: str | None = "root"
    lines: int | None = 50
    user_data: str | None = None


app = FastAPI(title="cloudx compute-worker", version="1.0.0")


def preflight() -> dict:
    kvm_available = Path("/dev/kvm").exists()
    tun_available = Path("/dev/net/tun").exists()
    qemu_img_available = tool_available("qemu-img")
    cloud_init_tool_available = tool_available("cloud-localds") or tool_available("genisoimage")
    ovs_available = tool_available("ovs-vsctl")
    br_int_exists = False
    if ovs_available:
        br_int_exists = run(["ovs-vsctl", "br-exists", OVS_BRIDGE], check=False).returncode == 0
    libvirt_connected = False
    libvirt_error = None
    try:
        conn = libvirt_driver.connect()
        conn.close()
        libvirt_connected = True
    except Exception as exc:
        libvirt_error = str(exc)
    return {
        "status": "ok",
        "service": "compute-worker",
        "libvirt_uri": LIBVIRT_URI,
        "libvirt_connected": libvirt_connected,
        "libvirt_error": libvirt_error,
        "kvm_available": kvm_available,
        "tun_available": tun_available,
        "ovs_available": ovs_available,
        "br_int_exists": br_int_exists,
        "qemu_img_available": qemu_img_available,
        "cloud_init_tool_available": cloud_init_tool_available,
    }


def run_worker(fn, *args):
    try:
        return fn(*args)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/health")
def health():
    return preflight()


# ── Lifecycle ─────────────────────────────────────────────────────────────────

@app.post("/servers/{server_id}/spawn")
def spawn(server_id: str, payload: ServerAction):
    return run_worker(worker.spawn_server, server_id, payload.model_dump(exclude_none=True))


@app.post("/servers/{server_id}/start")
def start(server_id: str, payload: ServerAction):
    return run_worker(worker.start_server, payload.model_dump(exclude_none=True))


@app.post("/servers/{server_id}/stop")
def stop(server_id: str, payload: ServerAction):
    return run_worker(worker.stop_server, payload.model_dump(exclude_none=True))


@app.post("/servers/{server_id}/reboot")
def reboot(server_id: str, payload: ServerAction):
    return run_worker(worker.reboot_server, payload.model_dump(exclude_none=True))


@app.post("/servers/{server_id}/pause")
def pause(server_id: str, payload: ServerAction):
    return run_worker(worker.pause_server, payload.model_dump(exclude_none=True))


@app.post("/servers/{server_id}/resume")
def resume(server_id: str, payload: ServerAction):
    return run_worker(worker.resume_server, payload.model_dump(exclude_none=True))


@app.post("/servers/{server_id}/suspend")
def suspend(server_id: str, payload: ServerAction):
    return run_worker(worker.suspend_server, payload.model_dump(exclude_none=True))


@app.post("/servers/{server_id}/unsuspend")
def unsuspend(server_id: str, payload: ServerAction):
    return run_worker(worker.unsuspend_server, payload.model_dump(exclude_none=True))


@app.post("/servers/{server_id}/resize")
def resize(server_id: str, payload: ServerAction):
    return run_worker(worker.resize_server, payload.model_dump(exclude_none=True))


@app.post("/servers/{server_id}/rebuild")
def rebuild(server_id: str, payload: ServerAction):
    return run_worker(worker.rebuild_server, server_id, payload.model_dump(exclude_none=True))


@app.post("/servers/{server_id}/rescue")
def rescue(server_id: str, payload: ServerAction):
    return run_worker(worker.rescue_server, server_id, payload.model_dump(exclude_none=True))


@app.post("/servers/{server_id}/unrescue")
def unrescue(server_id: str, payload: ServerAction):
    return run_worker(worker.unrescue_server, server_id, payload.model_dump(exclude_none=True))


@app.post("/servers/{server_id}/password")
def set_password(server_id: str, payload: ServerAction):
    return run_worker(worker.set_server_password, payload.model_dump(exclude_none=True))


@app.post("/servers/{server_id}/logs")
def get_logs(server_id: str, payload: ServerAction):
    return run_worker(worker.get_server_logs, payload.model_dump(exclude_none=True))


# ── Delete ─────────────────────────────────────────────────────────────────────

@app.delete("/servers/{server_id}")
def delete(server_id: str, payload: ServerAction | None = None):
    return run_worker(worker.delete_server, server_id, payload.model_dump(exclude_none=True) if payload else {})


# ── Volumes ───────────────────────────────────────────────────────────────────

@app.post("/servers/{server_id}/volumes/attach")
def attach_volume(server_id: str, payload: ServerAction):
    return run_worker(volume_manager.attach_volume, payload.model_dump(exclude_none=True))


@app.post("/servers/{server_id}/volumes/detach")
def detach_volume(server_id: str, payload: ServerAction):
    return run_worker(volume_manager.detach_volume, payload.model_dump(exclude_none=True))


# ── Console ───────────────────────────────────────────────────────────────────

@app.post("/servers/{server_id}/console-info")
def console_info(server_id: str, payload: ServerAction):
    data = payload.model_dump(exclude_none=True)
    domain = data.get("libvirt_domain_name")
    if not domain:
        raise HTTPException(status_code=404, detail="server has no libvirt domain")
    return run_worker(libvirt_driver.vnc_info, domain)


# ── Ports ─────────────────────────────────────────────────────────────────────

@app.post("/servers/{server_id}/ports/attach")
def attach_port(server_id: str, payload: ServerAction):
    data = payload.model_dump(exclude_none=True)
    data["id"] = server_id
    return run_worker(worker.attach_port, data)


@app.post("/servers/{server_id}/ports/detach")
def detach_port(server_id: str, payload: ServerAction):
    data = payload.model_dump(exclude_none=True)
    data["id"] = server_id
    return run_worker(worker.detach_port, data)
