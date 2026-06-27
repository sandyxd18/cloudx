"""
cloudx Compute Worker — AMQP Consumer (nova-compute equivalent)
================================================================

Consumes messages from RabbitMQ queue ``compute``.
Uses existing libvirt/worker modules for hypervisor operations.
DB updates go through the Conductor service via RPC.

All lifecycle operations (spawn, delete, start, stop, etc.) are async
RPC Cast handlers. Console log and VNC info are RPC Call handlers
(they return a reply to the caller).
"""

from __future__ import annotations

import logging
import os
import sys
import time

import requests

# Append parent so shared/ is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from shared.rpc import RPCClient, RPCConsumer  # noqa: E402

from . import libvirt_driver, volume_manager, worker  # noqa: E402
from .config import LIBVIRT_URI, OVS_BRIDGE, IMAGE_API_URL, BLOCK_STORE_API_URL  # noqa: E402
from .utils import run, tool_available  # noqa: E402
from pathlib import Path  # noqa: E402

# ── Config ────────────────────────────────────────────────────────────────────

RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://cloudx:cloudx@rabbitmq:5672/")
LOG_LEVEL    = os.getenv("LOG_LEVEL", "INFO")

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(name)s] %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("compute-worker")

# Cached resolved URL for block-store-api (probed at first use)
_resolved_block_store_url: str = ""

# RPC client for calling conductor
_rpc = RPCClient(RABBITMQ_URL)


# ═══════════════════════════════════════════════════════════════════════════════
# Conductor helpers
# ═══════════════════════════════════════════════════════════════════════════════

def conductor_update_server(server_id: str, fields: dict) -> None:
    """Call conductor to update server fields (status, host, mac, etc.)."""
    _rpc.cast("conductor", "update_server", {
        "server_id": server_id,
        "fields": fields,
    })


def conductor_mark_error(server_id: str, error_message: str) -> None:
    """Call conductor to mark server as ERROR."""
    _rpc.cast("conductor", "mark_server_error", {
        "server_id": server_id,
        "error_message": error_message,
    })


def conductor_delete_record(server_id: str) -> None:
    """Call conductor to hard-delete server record."""
    _rpc.cast("conductor", "delete_server_record", {
        "server_id": server_id,
    })


# ═══════════════════════════════════════════════════════════════════════════════
# HTTP helpers (for calling other services)
# ═══════════════════════════════════════════════════════════════════════════════

def _http_post(url: str, payload: dict, timeout: int = 60) -> dict | None:
    try:
        response = requests.post(url, json=payload, timeout=timeout)
        if response.status_code < 400:
            return response.json()
        log.warning("HTTP POST %s → %d: %s", url, response.status_code, response.text[:200])
    except Exception as exc:
        log.warning("HTTP POST %s failed: %s", url, exc)
    return None


def _http_delete(url: str, timeout: int = 20) -> None:
    try:
        requests.delete(url, timeout=timeout)
    except Exception:
        pass


def _probe_block_store_url(base_url: str) -> str:
    """Find a reachable block-store-api URL from a set of candidates.

    When compute-worker runs in host network mode (network_mode: host),
    Docker service names are not resolvable. We try several candidates
    and return the first one that responds.
    """
    global _resolved_block_store_url
    if _resolved_block_store_url:
        return _resolved_block_store_url

    candidates = [
        base_url,                          # from env (may be 127.0.0.1 or docker name)
        "http://127.0.0.1:8004",           # host-mode direct
        "http://172.17.0.1:8004",          # Docker bridge gateway
    ]
    # Deduplicate while preserving order
    seen = set()
    unique = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            unique.append(c)

    for url in unique:
        try:
            r = requests.get(f"{url}/volume-types", timeout=3)
            if r.status_code < 500:
                log.info("block-store-api reachable at %s", url)
                _resolved_block_store_url = url
                return url
        except Exception:
            pass
    log.warning("No block-store-api URL responded; defaulting to %s", base_url)
    _resolved_block_store_url = base_url
    return base_url


def wait_for_volume_ready(
    volume_id: str,
    block_store_url: str,
    timeout_sec: int = 300,
    poll_interval: float = 2.0,
) -> dict:
    """Poll block-store API until volume is 'available' with a valid path.

    Volume creation is async (block-store-worker via RabbitMQ). compute-api
    dispatches build_instance immediately after POST /internal/volumes, so
    compute-worker may receive an empty path. We must wait here.

    Raises RuntimeError if volume reaches error status or timeout expires.
    """
    # Find the correct base URL (important when running in host network mode)
    resolved_url = _probe_block_store_url(block_store_url)
    url = f"{resolved_url}/internal/volumes/{volume_id}"
    deadline = time.time() + timeout_sec
    log.info("Waiting for volume %s to become available (timeout=%ds, url=%s)...",
             volume_id, timeout_sec, url)
    while time.time() < deadline:
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                vol = resp.json()
                status = vol.get("status", "")
                path = vol.get("path", "")
                if status == "available" and path:
                    log.info("Volume %s is ready at %s", volume_id, path)
                    return vol
                if status == "error":
                    raise RuntimeError(
                        f"Volume {volume_id} entered error state: {vol.get('error_message', 'unknown')}"
                    )
                log.debug("Volume %s status=%s path=%r — waiting...", volume_id, status, path)
        except RuntimeError:
            raise
        except Exception as exc:
            log.debug("Volume poll failed: %s", exc)
        time.sleep(poll_interval)
    raise RuntimeError(
        f"Timed out waiting for volume {volume_id} to become available after {timeout_sec}s"
    )



# ═══════════════════════════════════════════════════════════════════════════════
# Health check
# ═══════════════════════════════════════════════════════════════════════════════

def handle_health(args: dict) -> dict:
    """RPC Call — return worker health status."""
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


# ═══════════════════════════════════════════════════════════════════════════════
# Lifecycle handlers (RPC Cast — no reply expected)
# ═══════════════════════════════════════════════════════════════════════════════

def handle_build_instance(args: dict) -> None:
    """Spawn a new VM via libvirt. Orchestrates full build flow."""
    server_id = args.get("id")
    root_volume_id = args.get("_root_volume_id")
    block_store_url = args.get("_block_store_url", BLOCK_STORE_API_URL)
    try:
        # ── Wait for root volume to be ready (async creation race condition) ──
        # block-store-worker creates the LV asynchronously via RabbitMQ.
        # compute-api sends build_instance immediately after POST /internal/volumes,
        # so root_volume.path may be empty. Poll until volume is available.
        if root_volume_id:
            ready_volume = wait_for_volume_ready(root_volume_id, block_store_url)
            # Inject the real path into args so worker.spawn_server can use it
            if args.get("root_volume") is not None:
                args["root_volume"]["path"] = ready_volume.get("path", "")
            else:
                args["root_volume"] = ready_volume

        result = worker.spawn_server(server_id, args)

        # Update server with hypervisor details (host, mac, tap, etc.)
        update_fields = {}
        for field in ("host", "instance_dir", "disk_path", "config_drive_path",
                      "libvirt_domain_name", "tap_name", "mac_address", "ip_address"):
            if field in result:
                update_fields[field] = result[field]

        # Attach root volume via block-store API
        if root_volume_id:
            bs_url = _probe_block_store_url(block_store_url)
            _http_post(
                f"{bs_url}/internal/volumes/{root_volume_id}/attach-server",
                {"server_id": server_id, "target_device": "sda"},
            )

        # Set status ACTIVE via conductor
        update_fields["status"] = "ACTIVE"
        update_fields["error_message"] = None
        conductor_update_server(server_id, update_fields)

        log.info("VM %s spawned successfully", server_id)

    except Exception as exc:
        log.error("Failed to spawn VM %s: %s", server_id, exc, exc_info=True)
        conductor_mark_error(server_id, str(exc))


def handle_delete_instance(args: dict) -> None:
    """Destroy a VM and clean up resources."""
    server_id = args.get("id")
    original_name = args.get("_original_name", "")
    keep_root_volume = args.get("_keep_root_volume", False)
    root_volume_id = args.get("_root_volume_id")
    port_id = args.get("_port_id")
    extra_port_ids = args.get("_extra_port_ids", "")
    block_store_url = args.get("_block_store_url", BLOCK_STORE_API_URL)
    networking_url = args.get("_networking_url", "http://localhost:8001")

    try:
        # 1. Destroy libvirt domain
        try:
            worker.delete_server(server_id, args)
        except Exception as exc:
            log.warning("Worker delete for %s: %s", server_id, exc)

        # 2. Release volumes via block-store API (use probed URL for host network mode)
        bs_url = _probe_block_store_url(block_store_url)
        _http_post(f"{bs_url}/servers/{server_id}/volumes/detach-all", {})
        if root_volume_id and not keep_root_volume:
            _http_delete(f"{bs_url}/internal/volumes/{root_volume_id}")

        # 3. Delete ports via networking API
        port_ids = [port_id] if port_id else []
        if extra_port_ids:
            port_ids.extend([p for p in extra_port_ids.split(",") if p.strip()])
        for pid in port_ids:
            if pid:
                _http_delete(f"{networking_url}/ports/{pid}")

        # 4. Update status via conductor
        conductor_update_server(server_id, {
            "status": "DELETED",
        })

        log.info("VM %s deleted successfully", server_id)

    except Exception as exc:
        log.error("Failed to delete VM %s: %s", server_id, exc, exc_info=True)
        conductor_mark_error(server_id, f"delete failed: {exc}")


def _simple_action(method_name: str, success_status: str, args: dict) -> None:
    """Generic handler for simple lifecycle actions (start/stop/etc.)."""
    server_id = args.get("id")
    try:
        fn = getattr(worker, method_name)
        fn(args)
        conductor_update_server(server_id, {"status": success_status, "error_message": None})
        log.info("%s %s → %s", method_name, server_id, success_status)
    except Exception as exc:
        log.error("%s %s failed: %s", method_name, server_id, exc, exc_info=True)
        conductor_mark_error(server_id, str(exc))


def handle_start(args: dict) -> None:
    _simple_action("start_server", "ACTIVE", args)

def handle_stop(args: dict) -> None:
    _simple_action("stop_server", "SHUTOFF", args)

def handle_reboot(args: dict) -> None:
    _simple_action("reboot_server", "ACTIVE", args)

def handle_pause(args: dict) -> None:
    _simple_action("pause_server", "PAUSED", args)

def handle_resume(args: dict) -> None:
    _simple_action("resume_server", "ACTIVE", args)

def handle_suspend(args: dict) -> None:
    _simple_action("suspend_server", "SUSPENDED", args)

def handle_unsuspend(args: dict) -> None:
    _simple_action("unsuspend_server", "ACTIVE", args)

def handle_resize(args: dict) -> None:
    _simple_action("resize_server", "ACTIVE", args)


def handle_rebuild(args: dict) -> None:
    server_id = args.get("id")
    try:
        worker.rebuild_server(server_id, args)
        conductor_update_server(server_id, {"status": "ACTIVE", "error_message": None})
    except Exception as exc:
        log.error("rebuild %s failed: %s", server_id, exc, exc_info=True)
        conductor_mark_error(server_id, str(exc))


def handle_rescue(args: dict) -> None:
    server_id = args.get("id")
    try:
        worker.rescue_server(server_id, args)
        # status stays RESCUE (already set by API)
        log.info("rescue %s done", server_id)
    except Exception as exc:
        log.error("rescue %s failed: %s", server_id, exc, exc_info=True)
        conductor_mark_error(server_id, str(exc))


def handle_unrescue(args: dict) -> None:
    server_id = args.get("id")
    try:
        worker.unrescue_server(server_id, args)
        conductor_update_server(server_id, {"status": "ACTIVE", "error_message": None})
    except Exception as exc:
        log.error("unrescue %s failed: %s", server_id, exc, exc_info=True)
        conductor_mark_error(server_id, str(exc))


def handle_set_password(args: dict) -> None:
    server_id = args.get("id")
    try:
        worker.set_server_password(args)
        log.info("password set for %s", server_id)
    except Exception as exc:
        log.error("set_password %s failed: %s", server_id, exc, exc_info=True)


# ═══════════════════════════════════════════════════════════════════════════════
# RPC Call handlers (synchronous — must return a reply)
# ═══════════════════════════════════════════════════════════════════════════════

def handle_get_logs(args: dict) -> dict:
    """RPC Call — return console log output."""
    return worker.get_server_logs(args)


def handle_get_console_info(args: dict) -> dict:
    """RPC Call — return VNC console info."""
    domain = args.get("libvirt_domain_name")
    if not domain:
        return {"error": "server has no libvirt domain"}
    return libvirt_driver.vnc_info(domain)


# ═══════════════════════════════════════════════════════════════════════════════
# Volume/Port hotplug handlers (RPC Cast)
# ═══════════════════════════════════════════════════════════════════════════════

def handle_attach_volume(args: dict) -> None:
    server_id = args.get("id")
    try:
        volume_manager.attach_volume(args)
        log.info("volume attached to %s", server_id)
    except Exception as exc:
        log.error("attach_volume %s failed: %s", server_id, exc, exc_info=True)


def handle_detach_volume(args: dict) -> None:
    server_id = args.get("id")
    try:
        volume_manager.detach_volume(args)
        log.info("volume detached from %s", server_id)
    except Exception as exc:
        log.error("detach_volume %s failed: %s", server_id, exc, exc_info=True)


def handle_attach_port(args: dict) -> None:
    server_id = args.get("id")
    data = dict(args)
    data["id"] = server_id
    try:
        worker.attach_port(data)
        log.info("port attached to %s", server_id)
    except Exception as exc:
        log.error("attach_port %s failed: %s", server_id, exc, exc_info=True)


def handle_detach_port(args: dict) -> None:
    server_id = args.get("id")
    data = dict(args)
    data["id"] = server_id
    try:
        worker.detach_port(data)
        log.info("port detached from %s", server_id)
    except Exception as exc:
        log.error("detach_port %s failed: %s", server_id, exc, exc_info=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Handler dispatch table
# ═══════════════════════════════════════════════════════════════════════════════

HANDLERS = {
    # Lifecycle (Cast)
    "build_instance":   handle_build_instance,
    "delete_instance":  handle_delete_instance,
    "start":            handle_start,
    "stop":             handle_stop,
    "reboot":           handle_reboot,
    "pause":            handle_pause,
    "resume":           handle_resume,
    "suspend":          handle_suspend,
    "unsuspend":        handle_unsuspend,
    "resize":           handle_resize,
    "rebuild":          handle_rebuild,
    "rescue":           handle_rescue,
    "unrescue":         handle_unrescue,
    "set_password":     handle_set_password,
    # Call (sync reply)
    "health":           handle_health,
    "get_logs":         handle_get_logs,
    "get_console_info": handle_get_console_info,
    # Hotplug (Cast)
    "attach_volume":    handle_attach_volume,
    "detach_volume":    handle_detach_volume,
    "attach_port":      handle_attach_port,
    "detach_port":      handle_detach_port,
}


# ═══════════════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    log.info("=" * 60)
    log.info("cloudx Compute Worker starting (AMQP consumer)")
    log.info("  RabbitMQ: %s", RABBITMQ_URL.split("@")[-1])
    log.info("  Libvirt:  %s", LIBVIRT_URI)
    log.info("  Handlers: %s", ", ".join(HANDLERS.keys()))
    log.info("=" * 60)

    # Wait for RabbitMQ to be ready
    for attempt in range(30):
        try:
            _rpc._ensure_connection()
            log.info("RabbitMQ connection OK")
            break
        except Exception as exc:
            log.warning("Waiting for RabbitMQ (%d/30): %s", attempt + 1, exc)
            time.sleep(2)
    else:
        log.error("Could not connect to RabbitMQ after 30 attempts")
        sys.exit(1)

    consumer = RPCConsumer(
        queue_name="compute",
        handlers=HANDLERS,
        rabbitmq_url=RABBITMQ_URL,
    )
    consumer.run()


if __name__ == "__main__":
    main()
