from __future__ import annotations
from shared.config import load_service_config
load_service_config("compute-worker")

"""
cloudx Compute Worker — AMQP Consumer (Compute Hypervisor Worker)
================================================================

Consumes messages from RabbitMQ queue ``compute``.
Uses existing libvirt/worker modules for hypervisor operations.
DB updates go through the Conductor service via RPC.

All lifecycle operations (spawn, delete, start, stop, etc.) are async
RPC Cast handlers. Console log and VNC info are RPC Call handlers
(they return a reply to the caller).

A background thread (``sync_power_states_loop``) also runs alongside the AMQP
consumer, polling libvirt every ``SYNC_POWER_STATE_INTERVAL`` seconds (default
30s) to correct DB drift when a domain stops/disappears outside of our own
RPC handlers (crash, OOM, host reboot, `virsh destroy`, ...). See
``sync_power_states`` for the reconciliation rules.
"""


import logging
import os
import sys
import threading
import time
from collections import deque

import requests

# Append parent so shared/ is importable
from shared.rpc import RPCClient, RPCConsumer  # noqa: E402

from . import libvirt_driver, volume_manager, worker  # noqa: E402
from .config import LIBVIRT_URI, OVS_BRIDGE, IMAGE_API_URL, BLOCK_STORE_API_URL, VM_NAME_PREFIX  # noqa: E402
from shared.proc import CommandError, succeeds
from .utils import run, tool_available  # noqa: E402
from pathlib import Path  # noqa: E402

# ── Config ────────────────────────────────────────────────────────────────────

RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://cloudx:cloudx@127.0.0.1:5672/")
LOG_LEVEL    = os.getenv("LOG_LEVEL", "INFO")
COMPUTE_HOST = os.getenv("COMPUTE_HOST", os.getenv("NODE_NAME", "cloudx-compute-01"))
PLACEMENT_API_URL = os.getenv("PLACEMENT_API_URL", "http://192.168.10.10:8006")

def placement_registration_loop() -> None:
    """Background daemon loop — registers compute node in Placement API."""
    log.info("[placement] node registration loop started (host=%s)", COMPUTE_HOST)
    url = f"{PLACEMENT_API_URL.rstrip('/')}/internal/resource_providers"
    while True:
        try:
            r = requests.post(url, json={"name": COMPUTE_HOST}, timeout=5)
            if r.status_code in (200, 201, 409):
                log.info("[placement] node '%s' registered in Placement API", COMPUTE_HOST)
                break
        except Exception as exc:
            log.debug("[placement] registration retry for '%s': %s", COMPUTE_HOST, exc)
        time.sleep(10)
SYNC_POWER_STATE_INTERVAL = int(os.getenv("SYNC_POWER_STATE_INTERVAL", "30"))

# Instance monitoring (EC2-style "Monitoring" tab): sample every 10s, keep 24h.
# ponytail: in-memory ring buffer, lost on worker restart. Upgrade path is a
# dedicated monitoring service/timeseries DB — planned separately by the user.
METRICS_SAMPLE_INTERVAL = int(os.getenv("METRICS_SAMPLE_INTERVAL", "10"))
METRICS_RETENTION_HOURS = int(os.getenv("METRICS_RETENTION_HOURS", "24"))
METRICS_BUFFER_SIZE = max(1, (METRICS_RETENTION_HOURS * 3600) // METRICS_SAMPLE_INTERVAL)

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
# Power-state reconciliation (_sync_power_states reconciliation)
# ═══════════════════════════════════════════════════════════════════════════════
#
# Anything that stops a domain outside our own RPC handlers — `virsh destroy`,
# a libvirtd restart, an OOM kill, a host kernel/package upgrade tearing down
# qemu processes — leaves compute_servers.status stuck at whatever it was
# before (usually ACTIVE), because nothing tells the database. This loop is
# the fix: periodically diff what libvirt actually has against what the DB
# thinks, and correct the DB.
#
# Statuses mid-transition (BUILD, DELETING, REBOOT, REBUILD, RESIZE,
# VERIFY_RESIZE, RESCUE, PAUSING, SHELVED_OFFLOADED, UNSHELVING, ...) are left
# alone — an RPC handler already owns that server and will set its own
# terminal status. We only correct the steady states: ACTIVE, SHUTOFF, PAUSED,
# SUSPENDED.

# Steady-state statuses this loop is allowed to correct. Any other status
# means a handler is actively mid-flight — do not touch it.
_SYNC_MANAGED_STATUSES = {"ACTIVE", "SHUTOFF", "PAUSED", "SUSPENDED"}

# Expected DB status for each hypervisor-reported power state.
_EXPECTED_STATUS_FOR_POWER_STATE = {
    "RUNNING": "ACTIVE",
    "PAUSED": "PAUSED",
    # A domain that libvirt reports as SHUTOFF, or one missing entirely
    # (destroyed/undefined outside of our control), both mean: not running.
    "SHUTOFF": "SHUTOFF",
    "MISSING": "SHUTOFF",
}


AUTO_RECOVERY = os.getenv("AUTO_RECOVERY_ENABLED", "true").lower() in ("1", "true", "yes")


def sync_power_states() -> None:
    """One reconciliation pass: compare libvirt reality against the DB.

    Never raises — errors are logged and the next scheduled pass will retry.
    """
    try:
        result = _rpc.call("conductor", "get_host_servers", {"host": COMPUTE_HOST}, timeout=10)
    except Exception as exc:
        log.warning("[sync] could not fetch host servers from conductor: %s", exc)
        return
    servers = result.get("servers", [])
    if not servers:
        return

    try:
        domain_states = libvirt_driver.get_all_domain_states()
    except Exception as exc:
        log.warning("[sync] could not read libvirt domain states, skipping pass: %s", exc)
        return

    for server in servers:
        db_status = server.get("status")
        if db_status not in _SYNC_MANAGED_STATUSES:
            continue  # a handler owns this server right now (BUILD, DELETING, ...)

        domain_name = server.get("libvirt_domain_name")
        if not domain_name:
            continue  # not spawned yet / no hypervisor domain to check

        power_state = domain_states.get(domain_name, "MISSING")
        expected_status = _EXPECTED_STATUS_FOR_POWER_STATE[power_state]

        if expected_status != db_status:
            if AUTO_RECOVERY and db_status == "ACTIVE" and power_state in ("SHUTOFF", "MISSING"):
                log.warning(
                    "[auto-recovery] server %s (%s): domain '%s' is %s on hypervisor — attempting auto-restart",
                    server.get("name"), server.get("id"), domain_name, power_state,
                )
                try:
                    libvirt_driver.start(domain_name)
                    log.info("[auto-recovery] server %s restarted successfully", server.get("name"))
                    continue
                except Exception as exc:
                    log.error("[auto-recovery] failed to restart server %s: %s", server.get("name"), exc)

            log.warning(
                "[sync] server %s (%s): domain '%s' is %s on hypervisor but DB says %s — correcting to %s",
                server.get("name"), server.get("id"), domain_name, power_state, db_status, expected_status,
            )
            conductor_update_server(server["id"], {"status": expected_status})


def sync_power_states_loop() -> None:
    """Background daemon loop — runs sync_power_states() every SYNC_POWER_STATE_INTERVAL seconds."""
    log.info("[sync] power-state reconciliation loop started (interval=%ds, host=%s)",
              SYNC_POWER_STATE_INTERVAL, COMPUTE_HOST)
    while True:
        time.sleep(SYNC_POWER_STATE_INTERVAL)
        sync_power_states()


# ═══════════════════════════════════════════════════════════════════════════════
# Instance monitoring (EC2-style "Monitoring" tab) — sampling + ring buffer
# ═══════════════════════════════════════════════════════════════════════════════
#
# Every METRICS_SAMPLE_INTERVAL seconds, snapshot raw libvirt counters for every
# RUNNING instance and diff them against the previous sample to derive rates
# (CPU %, bytes/sec, IOPS). Samples are kept in a per-server ring buffer capped
# at METRICS_RETENTION_HOURS (24h @ 10s = 8640 points/server) — this is an
# in-process cache, not durable storage; it resets on worker restart.

# server_id -> deque of {timestamp (epoch seconds), cpu_percent, mem_used_mb,
#                         mem_total_mb, net_rx_bps, net_tx_bps, disk_read_bps,
#                         disk_write_bps, disk_read_iops, disk_write_iops}
_metrics_history: dict[str, deque] = {}
# server_id -> last raw counters, for delta/rate computation between samples.
_metrics_last_raw: dict[str, dict] = {}
_metrics_lock = threading.Lock()


def _compute_rate(current: dict, previous: dict | None) -> dict:
    """Diff two raw counter samples into per-second rates + a CPU percentage."""
    now_wall = time.time()
    mem_total_mb = round((current.get("mem_total_kb") or 0) / 1024, 1)
    mem_used_kb = current.get("mem_used_kb")
    mem_used_mb = round(mem_used_kb / 1024, 1) if mem_used_kb is not None else None

    sample = {
        "timestamp": now_wall,
        "cpu_percent": 0.0,
        "mem_used_mb": mem_used_mb,
        "mem_total_mb": mem_total_mb,
        "mem_percent": round((mem_used_mb / mem_total_mb) * 100, 1) if mem_used_mb and mem_total_mb else None,
        "net_rx_bps": 0.0,
        "net_tx_bps": 0.0,
        "disk_read_bps": 0.0,
        "disk_write_bps": 0.0,
        "disk_read_iops": 0.0,
        "disk_write_iops": 0.0,
    }

    if not previous:
        return sample  # first sample for this server — no delta possible yet

    elapsed = current["timestamp"] - previous["timestamp"]
    if elapsed <= 0:
        return sample

    nr_vcpus = max(1, current.get("nr_vcpus") or 1)
    cpu_delta_ns = current["cpu_time_ns"] - previous["cpu_time_ns"]
    # cpu_time_ns is cumulative ns of CPU consumed across all vCPUs.
    # % = (ns consumed / (elapsed_s * 1e9 * nr_vcpus)) * 100
    cpu_percent = (cpu_delta_ns / (elapsed * 1_000_000_000 * nr_vcpus)) * 100
    sample["cpu_percent"] = round(max(0.0, min(100.0, cpu_percent)), 1)

    def rate(key: str) -> float:
        delta = current.get(key, 0) - previous.get(key, 0)
        return round(max(0.0, delta) / elapsed, 1)

    sample["net_rx_bps"] = rate("net_rx_bytes")
    sample["net_tx_bps"] = rate("net_tx_bytes")
    sample["disk_read_bps"] = rate("disk_read_bytes")
    sample["disk_write_bps"] = rate("disk_write_bytes")
    sample["disk_read_iops"] = rate("disk_read_req")
    sample["disk_write_iops"] = rate("disk_write_req")
    return sample


def collect_metrics() -> None:
    """One sampling pass: read libvirt counters for every RUNNING server and record a rate sample."""
    try:
        result = _rpc.call("conductor", "get_host_servers", {"host": COMPUTE_HOST}, timeout=10)
    except Exception as exc:
        log.warning("[metrics] could not fetch host servers from conductor: %s", exc)
        return
    servers = result.get("servers", [])

    live_ids = set()
    for server in servers:
        domain_name = server.get("libvirt_domain_name")
        if not domain_name or server.get("status") not in ("ACTIVE", "PAUSED"):
            continue
        server_id = server["id"]
        live_ids.add(server_id)
        try:
            raw = libvirt_driver.get_domain_raw_counters(domain_name, server.get("tap_name"))
        except Exception as exc:
            log.debug("[metrics] could not read counters for %s: %s", domain_name, exc)
            continue
        if raw is None:
            continue  # domain not active despite DB status

        with _metrics_lock:
            previous = _metrics_last_raw.get(server_id)
            sample = _compute_rate(raw, previous)
            _metrics_last_raw[server_id] = raw
            buf = _metrics_history.setdefault(server_id, deque(maxlen=METRICS_BUFFER_SIZE))
            buf.append(sample)

    # Drop buffers/raw-state for servers that are no longer live (deleted, stopped)
    # so memory doesn't grow unbounded across the worker's lifetime.
    with _metrics_lock:
        stale = set(_metrics_last_raw) - live_ids
        for server_id in stale:
            _metrics_last_raw.pop(server_id, None)


def get_metrics_history(server_id: str, since_epoch: float | None = None) -> list[dict]:
    """Return recorded samples for a server, optionally filtered to `timestamp >= since_epoch`."""
    with _metrics_lock:
        buf = _metrics_history.get(server_id)
        samples = list(buf) if buf else []
    if since_epoch is not None:
        samples = [s for s in samples if s["timestamp"] >= since_epoch]
    return samples


def collect_metrics_loop() -> None:
    """Background daemon loop — samples every METRICS_SAMPLE_INTERVAL seconds."""
    log.info("[metrics] instance monitoring loop started (interval=%ds, retention=%dh, buffer=%d samples/server)",
              METRICS_SAMPLE_INTERVAL, METRICS_RETENTION_HOURS, METRICS_BUFFER_SIZE)
    while True:
        time.sleep(METRICS_SAMPLE_INTERVAL)
        collect_metrics()


def handle_get_metrics(args: dict) -> dict:
    """RPC Call — return recorded monitoring samples for one server.

    Expected args:: {"server_id": "uuid", "since_epoch": 1234567890.0 (optional)}
    """
    server_id = args.get("server_id")
    if not server_id:
        return {"error": "server_id required"}
    samples = get_metrics_history(server_id, args.get("since_epoch"))
    return {"server_id": server_id, "samples": samples, "sample_interval": METRICS_SAMPLE_INTERVAL}


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
        resp = requests.delete(url, timeout=timeout)
        if resp.status_code >= 400:
            # Never swallow cleanup failures silently — a silent 401 here is
            # exactly how orphaned ports accumulated before /internal endpoints.
            log.warning("DELETE %s → HTTP %d: %s", url, resp.status_code, resp.text[:200])
    except Exception as exc:
        log.warning("DELETE %s failed: %s", url, exc)


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
        except Exception as exc:
            log.debug("block-store-api not reachable at %s: %s", url, exc)
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
                if status == "available":
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
        try:
            br_int_exists = succeeds(["ovs-vsctl", "br-exists", OVS_BRIDGE])
        except CommandError as exc:
            log.warning("could not check OVS bridge %s: %s", OVS_BRIDGE, exc)
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

        # 3. Delete ports via networking API (internal endpoint — no auth,
        #    same service-to-service pattern as block-store's /internal/volumes)
        port_ids = [port_id] if port_id else []
        if extra_port_ids:
            port_ids.extend([p for p in extra_port_ids.split(",") if p.strip()])
        for pid in port_ids:
            if pid:
                _http_delete(f"{networking_url}/internal/ports/{pid}")

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


def handle_shelve(args: dict) -> None:
    """
    Shelve a server:
    1. Stop VM (if running)
    2. Create a snapshot image of the root volume via block-store
    3. Mark server SHELVED_OFFLOADED in conductor
    """
    server_id = args.get("id")
    root_volume_id = args.get("root_volume_id") or args.get("_root_volume_id")
    image_api_url = args.get("_image_api_url", IMAGE_API_URL)
    block_store_url = args.get("_block_store_url", BLOCK_STORE_API_URL)

    try:
        # Stop VM
        domain_name = args.get("libvirt_domain_name")
        if domain_name:
            try:
                import libvirt as _libvirt  # noqa: F401  (availability probe: falls back to virsh when missing)
                conn = libvirt_driver.connect()
                domain = conn.lookupByName(domain_name)
                if domain.isActive():
                    domain.destroy()
                conn.close()
            except Exception as exc:
                log.warning("shelve: could not stop VM %s: %s", domain_name, exc)

        # Create volume snapshot
        shelved_image_id = None
        if root_volume_id:
            try:
                bsu = _probe_block_store_url(block_store_url)
                snap_resp = requests.post(
                    f"{bsu}/internal/volumes/{root_volume_id}/snapshots",
                    json={"name": f"shelved-{server_id}"},
                    timeout=60,
                )
                if snap_resp.status_code < 300:
                    snap = snap_resp.json()
                    # Import snapshot as an image record
                    img_resp = requests.post(
                        f"{image_api_url}/images/import-path",
                        json={
                            "name": f"shelved-{server_id}",
                            "path": snap.get("path", ""),
                            "disk_format": "qcow2",
                            "container_format": "bare",
                            "visibility": "private",
                        },
                        timeout=60,
                    )
                    if img_resp.status_code < 300:
                        shelved_image_id = img_resp.json().get("id")
                        log.info("shelve: created shelved image %s for server %s", shelved_image_id, server_id)
                    else:
                        log.warning("shelve: image import failed: %s", img_resp.text[:200])
                else:
                    log.warning("shelve: snapshot failed: %s", snap_resp.text[:200])
            except Exception as exc:
                log.warning("shelve: volume snapshot failed: %s", exc)

        # Update conductor
        fields = {"status": "SHELVED_OFFLOADED"}
        if shelved_image_id:
            fields["shelved_image_id"] = shelved_image_id
        conductor_update_server(server_id, fields)
        log.info("shelve: server %s is SHELVED_OFFLOADED", server_id)

    except Exception as exc:
        log.error("shelve %s failed: %s", server_id, exc, exc_info=True)
        conductor_mark_error(server_id, str(exc))


def handle_unshelve(args: dict) -> None:
    """
    Unshelve a server:
    1. Restore volume from shelved image (qemu-img convert or rebase)
    2. Start the VM
    3. Mark server ACTIVE in conductor
    """
    server_id = args.get("id")
    shelved_image_id = args.get("shelved_image_id")
    root_volume_id = args.get("root_volume_id") or args.get("_root_volume_id") or args.get("root_volume", {}).get("id")
    image_api_url = args.get("_image_api_url", IMAGE_API_URL)

    try:
        # If we have a shelved image, restore it
        if shelved_image_id and root_volume_id:
            try:
                bsu = _probe_block_store_url(args.get("_block_store_url", BLOCK_STORE_API_URL))
                vol_resp = requests.get(f"{bsu}/internal/volumes/{root_volume_id}", timeout=10)
                if vol_resp.status_code == 200:
                    vol = vol_resp.json()
                    vol_path = vol.get("path", "")
                    # Download shelved image and restore to volume path
                    img_resp = requests.get(f"{image_api_url}/images/{shelved_image_id}/download", stream=True, timeout=300)
                    if img_resp.status_code == 200 and vol_path:
                        import tempfile
                        with tempfile.NamedTemporaryFile(suffix=".qcow2", delete=False) as tmp:
                            for chunk in img_resp.iter_content(chunk_size=65536):
                                if chunk:
                                    tmp.write(chunk)
                            tmp_path = tmp.name
                        run(["qemu-img", "convert", "-O", "raw", tmp_path, vol_path], timeout=600)
                        import os as _os
                        _os.unlink(tmp_path)
                        log.info("unshelve: volume %s restored from image %s", root_volume_id, shelved_image_id)
            except Exception as exc:
                log.warning("unshelve: volume restore failed: %s", exc)

        # Start the VM
        domain_name = args.get("libvirt_domain_name")
        if domain_name:
            try:
                conn = libvirt_driver.connect()
                domain = conn.lookupByName(domain_name)
                if not domain.isActive():
                    domain.create()
                conn.close()
                log.info("unshelve: VM %s started", domain_name)
            except Exception as exc:
                log.warning("unshelve: could not start VM %s: %s", domain_name, exc)

        conductor_update_server(server_id, {
            "status": "ACTIVE",
            "shelved_image_id": None,
        })
        log.info("unshelve: server %s is ACTIVE", server_id)

    except Exception as exc:
        log.error("unshelve %s failed: %s", server_id, exc, exc_info=True)
        conductor_mark_error(server_id, str(exc))


def handle_prefetch_image(args: dict) -> dict:
    image_ref = args.get("image_id") or args.get("image_ref")
    if not image_ref:
        return {"status": "error", "message": "image_id required"}
    try:
        from .image_manager import fetch_image_meta, download_image
        meta = fetch_image_meta(image_ref)
        path = download_image(meta)
        log.info("Prefetched image %s to local cache: %s", image_ref, path)
        return {"status": "cached", "image_id": image_ref, "path": str(path)}
    except Exception as exc:
        log.error("Failed to prefetch image %s: %s", image_ref, exc)
        return {"status": "error", "message": str(exc)}


def handle_live_migrate(args: dict) -> None:
    """Live migrate a running VM to another compute node (Fase 6.1)."""
    server_id = args.get("server_id")
    target_host = args.get("target_host")
    log.info("Live migrate request: server=%s target=%s", server_id, target_host)
    if not server_id or not target_host:
        conductor_mark_error(server_id or "unknown", "server_id and target_host required for live migration")
        return

    try:
        import libvirt
        conn = libvirt.open(LIBVIRT_URI)
        if not conn:
            raise RuntimeError("failed to open libvirt connection")

        dom = None
        try:
            dom = conn.lookupByUUIDString(server_id)
        except Exception:  # best-effort: falls back to lookup by name
            try:
                dom = conn.lookupByName(f"{VM_NAME_PREFIX}-{server_id[:8]}")
            except Exception:  # best-effort: dom stays None and a 'domain not found' error is raised below
                pass

        if not dom:
            raise RuntimeError(f"domain for server {server_id} not found")

        # Target URI for migration (shared storage required)
        target_uri = f"qemu+ssh://{target_host}/system"

        # Perform live migration with persistent and undefine-source flags
        flags = libvirt.VIR_MIGRATE_LIVE | libvirt.VIR_MIGRATE_PERSIST_DEST | libvirt.VIR_MIGRATE_UNDEFINE_SOURCE
        new_dom = dom.migrateToURI3(target_uri, flags, None, None)

        if new_dom:
            log.info("Live migration of server %s to %s succeeded", server_id, target_host)
            conductor_update_server(server_id, {
                "status": "ACTIVE",
                "host": target_host,
            })
        else:
            raise RuntimeError("migration returned None")
    except Exception as exc:
        log.exception("Live migration failed for server %s: %s", server_id, exc)
        conductor_mark_error(server_id, f"live migration failed: {exc}")


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
    "shelve":           handle_shelve,
    "unshelve":         handle_unshelve,
    "prefetch_image":   handle_prefetch_image,
    "live_migrate_instance": handle_live_migrate,
    # Call (sync reply)
    "health":           handle_health,
    "get_logs":         handle_get_logs,
    "get_console_info": handle_get_console_info,
    "get_metrics":      handle_get_metrics,
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

    threading.Thread(target=sync_power_states_loop, daemon=True).start()
    threading.Thread(target=collect_metrics_loop, daemon=True).start()
    threading.Thread(target=placement_registration_loop, daemon=True).start()

    consumer = RPCConsumer(
        queue_name="compute",
        handlers=HANDLERS,
        rabbitmq_url=RABBITMQ_URL,
    )
    consumer.run()


if __name__ == "__main__":
    main()
