from __future__ import annotations
from shared.config import load_service_config
load_service_config("telemetry-worker")
"""
cloudx Telemetry Worker — Metrics Collector
============================================

Background worker service (Phase 5.1) that:
  • Collects VM metrics from libvirt (virsh domstats)
  • Collects network metrics from OVN
  • Mengirim data ke telemetry-api via REST API

Tables:
  telemetry_metrics — time-series metrics
"""


import logging
import os
import time
import xml.etree.ElementTree as ET

import requests
from shared.proc import run


log = logging.getLogger("telemetry-worker")

# ── Config ────────────────────────────────────────────────────────────────────
TELEMETRY_API_URL = os.getenv("TELEMETRY_API_URL", "http://127.0.0.1:8010").rstrip("/")
COMPUTE_API_URL = os.getenv("COMPUTE_API_URL", "http://compute-api:8000").rstrip("/")
AUTH_API_URL = os.getenv("AUTH_API_URL", "http://auth-api:8000").rstrip("/")
AUTH_ADMIN_USERNAME = os.getenv("AUTH_ADMIN_USERNAME", "admin")
AUTH_ADMIN_PASSWORD = os.environ["AUTH_ADMIN_PASSWORD"]
COMPUTE_HOST = os.getenv("COMPUTE_HOST", "single-node")

RETENTION_DAYS = int(os.getenv("TELEMETRY_RETENTION_DAYS", "7"))
SAMPLE_INTERVAL = int(os.getenv("SAMPLE_INTERVAL", "30"))  # seconds
LIBVIRT_URI = os.getenv("LIBVIRT_URI", "qemu:///system")

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s [%(name)s] %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# ── Service Token ─────────────────────────────────────────────────────────────
_svc_token: str | None = None
_svc_token_exp: float = 0.0


def _get_svc_token() -> str | None:
    global _svc_token, _svc_token_exp
    if _svc_token and time.time() < _svc_token_exp:
        return _svc_token
    _svc_token = None
    try:
        r = requests.post(
            f"{AUTH_API_URL}/auth/tokens",
            json={"username": AUTH_ADMIN_USERNAME, "password": AUTH_ADMIN_PASSWORD},
            timeout=10,
        )
        if r.status_code in (200, 201):
            body = r.json()
            _svc_token = body["token"]
            _svc_token_exp = time.time() + float(body.get("expires_in", 3600)) - 60
            return _svc_token
    except Exception as e:
        log.warning("Failed to get service token: %s", e)
    return None


def _api_headers() -> dict:
    token = _get_svc_token()
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _api(method: str, url: str, **kw):
    """requests.request with service token; on 401 clear the cached token and retry once."""
    global _svc_token
    r = requests.request(method, url, headers=_api_headers(), **kw)
    if r.status_code == 401:
        _svc_token = None
        r = requests.request(method, url, headers=_api_headers(), **kw)
    return r


# ── Rate math (pure) ──────────────────────────────────────────────────────────

def cpu_percent(prev_ns: int, cur_ns: int, dt_s: float, vcpus: int) -> float:
    """CPU % of all vCPUs over the interval, clamped to 0-100."""
    if dt_s <= 0 or vcpus <= 0:
        return 0.0
    return min(100.0, max(0.0, (cur_ns - prev_ns) / (dt_s * 1e9 * vcpus) * 100))


def counter_rate(prev: float, cur: float, dt_s: float) -> float:
    """Per-second rate of a cumulative counter; a reset (cur < prev) reports 0."""
    if dt_s <= 0:
        return 0.0
    return max(0.0, (cur - prev) / dt_s)


# instance_id -> {"t": monotonic, "cpu_ns": int, "vcpus": int, <counter>: value}
_prev: dict[str, dict] = {}

_COUNTERS = ("disk_read_bytes", "disk_write_bytes", "disk_read_iops", "disk_write_iops",
             "net_rx_bytes", "net_tx_bytes", "net_rx_packets", "net_tx_packets")


def _apply_rates(instance_id: str, raw: dict, stats: dict) -> None:
    """Turn raw cumulative counters in `raw` into rates in `stats`; first sample only stores."""
    now = time.monotonic()
    cur = {"t": now, "cpu_ns": raw.get("cpu_ns"), "vcpus": raw.get("vcpus", 1)}
    cur.update({k: raw[k] for k in _COUNTERS if k in raw})
    prev = _prev.get(instance_id)
    _prev[instance_id] = cur
    if not prev:
        return
    dt = now - prev["t"]
    if cur["cpu_ns"] is not None and prev.get("cpu_ns") is not None:
        stats["cpu_percent"] = cpu_percent(prev["cpu_ns"], cur["cpu_ns"], dt, cur["vcpus"])
    for k in _COUNTERS:
        if k in cur and k in prev:
            stats[k] = counter_rate(prev[k], cur[k], dt)


# ── Libvirt helpers ───────────────────────────────────────────────────────────

try:
    import libvirt
    HAS_LIBVIRT = True
except ImportError:
    HAS_LIBVIRT = False
    log.warning("libvirt-python not available, using fallback shell commands")


def _get_libvirt_conn():
    if HAS_LIBVIRT:
        try:
            return libvirt.open(LIBVIRT_URI)
        except Exception as e:
            log.error("Failed to connect to libvirt: %s", e)
            return None
    return None


def _get_instance_stats_libvirt(dom, instance_id: str = "") -> dict:
    """Extract stats from a libvirt domain (cumulative counters converted to rates)."""
    stats: dict = {}
    raw: dict = {}
    try:
        cpu_stats = dom.getCPUStats(True)
        if cpu_stats:
            raw["cpu_ns"] = cpu_stats[0].get("cpu_time", 0)
            raw["vcpus"] = dom.info()[3]
    except Exception as e:
        log.debug("CPU stats error: %s", e)
    
    try:
        # Memory stats
        mem_stats = dom.memoryStats()
        if mem_stats:
            stats["memory_used_mb"] = mem_stats.get("rss", 0) / 1024  # KB to MB
            stats["memory_total_mb"] = mem_stats.get("actual", mem_stats.get("usable", 0)) / 1024
    except Exception as e:
        log.debug("Memory stats error: %s", e)
    
    try:
        tree = ET.fromstring(dom.XMLDesc())
    except Exception as e:
        log.debug("XMLDesc error: %s", e)
        tree = None

    if tree is not None:
        for disk in tree.findall(".//disk"):
            target = disk.find("target")
            dev = target.get("dev", "") if target is not None else ""
            if not dev:
                continue
            try:
                bs = dom.blockStats(dev)  # (rd_req, rd_bytes, wr_req, wr_bytes, errs)
                raw.update(disk_read_iops=bs[0], disk_read_bytes=bs[1],
                           disk_write_iops=bs[2], disk_write_bytes=bs[3])
                break
            except Exception as exc:
                log.debug("disk stats unavailable for %s: %s", dev, exc)

        for iface in tree.findall(".//interface"):
            target = iface.find("target")
            dev = target.get("dev", "") if target is not None else ""
            if not dev:
                continue
            try:
                ifs = dom.interfaceStats(dev)  # (rx_bytes, rx_packets, rx_errs, rx_drop, tx_bytes, tx_packets, ...)
                raw.update(net_rx_bytes=ifs[0], net_rx_packets=ifs[1],
                           net_tx_bytes=ifs[4], net_tx_packets=ifs[5])
                break
            except Exception as exc:
                log.debug("interface stats unavailable for %s: %s", dev, exc)

    _apply_rates(instance_id, raw, stats)
    return stats


def _get_instance_stats_shell(instance_name: str, instance_id: str = "") -> dict:
    """Fallback: get stats using virsh shell commands."""
    stats: dict = {}
    raw: dict = {}
    try:
        result = run(["virsh", "-c", LIBVIRT_URI, "domstats", instance_name], check=False, timeout=10)
        if result.returncode == 0:
            for line in result.stdout.split("\n"):
                line = line.strip()
                if line.startswith("cpu.time="):
                    raw["cpu_ns"] = int(line.split("=")[1])
                elif line.startswith("vcpu.current="):
                    raw["vcpus"] = int(line.split("=")[1])
                elif line.startswith("balloon.rss="):
                    stats["memory_used_mb"] = int(line.split("=")[1]) / 1024
                elif line.startswith("balloon.current="):
                    stats["memory_total_mb"] = int(line.split("=")[1]) / 1024
    except Exception as e:
        log.debug("Shell domstats error: %s", e)
    _apply_rates(instance_id or instance_name, raw, stats)
    return stats


# ── Instance discovery ────────────────────────────────────────────────────────

def _get_active_instances() -> list[dict]:
    """Get list of active instances from compute-api or libvirt directly."""
    instances = []
    
    # Try compute-api first
    try:
        r = _api("GET", f"{COMPUTE_API_URL}/servers?all_tenants=true&status=ACTIVE", timeout=10)
        if r.status_code in (200, 201):
            data = r.json()
            for s in data.get("servers", []):
                instances.append({
                    "id": s.get("id", s.get("uuid", "")),
                    "name": s.get("name", ""),
                    "project_id": s.get("project_id", s.get("tenant_id", "")),
                })
            return instances
    except Exception as e:
        log.debug("Failed to get instances from compute-api: %s", e)
    
    # Fallback: query libvirt directly
    conn = _get_libvirt_conn()
    if conn:
        try:
            for dom_id in conn.listDomainsID():
                dom = conn.lookupByID(dom_id)
                name = dom.name()
                # Extract UUID from name or domain
                uuid_str = dom.UUIDString()
                instances.append({
                    "id": uuid_str,
                    "name": name,
                    "project_id": None,
                })
        except Exception as e:
            log.error("Libvirt enumeration error: %s", e)
        finally:
            conn.close()
    
    return instances


# ── Metrics sender ────────────────────────────────────────────────────────────

def _send_metrics_batch(metrics: list[dict]) -> bool:
    """Send metrics batch to telemetry-api."""
    if not metrics:
        return True
    
    try:
        payload = []
        for m in metrics:
            payload.append({
                "instance_id": m["instance_id"],
                "project_id": m.get("project_id"),
                "metric_type": m["metric_type"],
                "value": m["value"],
                "unit": m.get("unit"),
                "host": COMPUTE_HOST,
            })
        
        r = _api("POST", f"{TELEMETRY_API_URL}/telemetry/metrics/batch", json=payload, timeout=30)
        if r.status_code in (200, 201):
            log.debug("Sent %d metrics", len(metrics))
            return True
        else:
            log.warning("Failed to send metrics: %s %s", r.status_code, r.text)
            return False
    except Exception as e:
        log.error("Error sending metrics: %s", e)
        return False


# ── Main collection loop ──────────────────────────────────────────────────────

def collect_and_send():
    """Collect metrics from all active instances and send to telemetry-api."""
    instances = _get_active_instances()
    if not instances:
        log.debug("No active instances found")
        return
    live = {i["id"] for i in instances}
    for k in [k for k in _prev if k not in live]:
        del _prev[k]

    all_metrics = []
    conn = _get_libvirt_conn()
    
    for inst in instances:
        instance_id = inst["id"]
        instance_name = inst.get("name", instance_id)
        
        stats = {}
        if conn:
            try:
                # Try to find domain by UUID or name
                try:
                    dom = conn.lookupByUUIDString(instance_id)
                except Exception:  # best-effort: falls back to lookup by name
                    try:
                        dom = conn.lookupByName(instance_name)
                    except Exception:  # best-effort: instance is not on this host; dom stays None
                        dom = None
                
                if dom:
                    stats = _get_instance_stats_libvirt(dom, instance_id)
            except Exception as e:
                log.debug("Error getting stats for %s: %s", instance_id, e)
        
        if not stats:
            # Fallback to shell
            stats = _get_instance_stats_shell(instance_name, instance_id)
        
        # Build metric records
        for metric_type, value in stats.items():
            if value is not None and isinstance(value, (int, float)):
                unit = None
                if "_mb" in metric_type:
                    unit = "MB"
                elif "_bytes" in metric_type:
                    unit = "B/s"
                elif "_iops" in metric_type:
                    unit = "iops"
                elif "_percent" in metric_type:
                    unit = "%"
                elif "_packets" in metric_type:
                    unit = "pps"
                
                all_metrics.append({
                    "instance_id": instance_id,
                    "project_id": inst.get("project_id"),
                    "metric_type": metric_type,
                    "value": float(value),
                    "unit": unit,
                })
    
    if conn:
        conn.close()
    
    # Send in batches of 100
    batch_size = 100
    for i in range(0, len(all_metrics), batch_size):
        batch = all_metrics[i:i + batch_size]
        _send_metrics_batch(batch)


def _cleanup_old_metrics() -> None:
    try:
        r = _api("DELETE", f"{TELEMETRY_API_URL}/telemetry/metrics/cleanup?days={RETENTION_DAYS}", timeout=60)
        if r.status_code >= 300:
            log.warning("Metrics cleanup failed: %s %s", r.status_code, r.text)
    except Exception as e:
        log.warning("Metrics cleanup error: %s", e)


def main_loop():
    """Main worker loop."""
    log.info("Telemetry Worker started (interval=%ds, host=%s)", SAMPLE_INTERVAL, COMPUTE_HOST)

    last_cleanup = None
    while True:
        if last_cleanup is None or time.monotonic() - last_cleanup >= 3600:
            last_cleanup = time.monotonic()
            _cleanup_old_metrics()
        try:
            collect_and_send()
        except Exception as e:
            log.exception("Error in collection loop: %s", e)
        
        time.sleep(SAMPLE_INTERVAL)


if __name__ == "__main__":
    main_loop()
