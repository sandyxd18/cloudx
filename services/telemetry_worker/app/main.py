from __future__ import annotations
"""
cloudx Telemetry Worker — Metrics Collector
============================================

Worker service (Fase 5.1) yang berjalan di background untuk:
  • Mengumpulkan metrik VM dari libvirt (virsh domstats)
  • Mengumpulkan metrik network dari OVN
  • Mengirim data ke telemetry-api via REST API

Tables:
  telemetry_metrics — time-series metrics
"""


import json
import logging
import os
import sys
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

log = logging.getLogger("telemetry-worker")

# ── Config ────────────────────────────────────────────────────────────────────
TELEMETRY_API_URL = os.getenv("TELEMETRY_API_URL", "http://127.0.0.1:8010").rstrip("/")
COMPUTE_API_URL = os.getenv("COMPUTE_API_URL", "http://compute-api:8000").rstrip("/")
AUTH_API_URL = os.getenv("AUTH_API_URL", "http://auth-api:8000").rstrip("/")
AUTH_ADMIN_USERNAME = os.getenv("AUTH_ADMIN_USERNAME", "admin")
AUTH_ADMIN_PASSWORD = os.getenv("AUTH_ADMIN_PASSWORD", "admin123")
COMPUTE_HOST = os.getenv("COMPUTE_HOST", "single-node")

SAMPLE_INTERVAL = int(os.getenv("SAMPLE_INTERVAL", "30"))  # seconds
LIBVIRT_URI = os.getenv("LIBVIRT_URI", "qemu:///system")

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s [%(name)s] %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# ── Service Token ─────────────────────────────────────────────────────────────
_svc_token: str | None = None


def _get_svc_token() -> str | None:
    global _svc_token
    if _svc_token:
        return _svc_token
    try:
        r = requests.post(
            f"{AUTH_API_URL}/auth/tokens",
            json={"username": AUTH_ADMIN_USERNAME, "password": AUTH_ADMIN_PASSWORD},
            timeout=10,
        )
        if r.status_code == 200:
            _svc_token = r.json()["token"]
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


def _get_instance_stats_libvirt(dom) -> dict:
    """Extract stats from a libvirt domain."""
    stats = {}
    try:
        # CPU stats
        cpu_stats = dom.getCPUStats(True)
        if cpu_stats and len(cpu_stats) > 0:
            cpu_time = cpu_stats[0].get("cpu_time", 0)
            # Convert to percentage (simplified)
            stats["cpu_percent"] = min(100.0, max(0.0, cpu_time / 1e9 / SAMPLE_INTERVAL * 100))
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
        # Block stats
        block_stats = dom.blockStats("vda") if "vda" in [d.get("name", "") for d in dom.blockInfo("vda")] else None
        if not block_stats:
            # Try to get all block devices
            tree = ET.fromstring(dom.XMLDesc())
            for disk in tree.findall(".//disk"):
                target = disk.find("target")
                if target is not None:
                    dev = target.get("dev", "")
                    if dev:
                        try:
                            bs = dom.blockStats(dev)
                            if bs:
                                stats["disk_read_bytes"] = bs[1]  # rd_bytes
                                stats["disk_write_bytes"] = bs[3]  # wr_bytes
                                stats["disk_read_iops"] = bs[0]  # rd_req
                                stats["disk_write_iops"] = bs[2]  # wr_req
                                break
                        except Exception:
                            pass
    except Exception as e:
        log.debug("Block stats error: %s", e)
    
    try:
        # Interface stats
        tree = ET.fromstring(dom.XMLDesc())
        for iface in tree.findall(".//interface"):
            target = iface.find("target")
            if target is not None:
                dev = target.get("dev", "")
                if dev:
                    try:
                        ifs = dom.interfaceStats(dev)
                        if ifs:
                            stats["net_rx_bytes"] = ifs[0]  # rx_bytes
                            stats["net_tx_bytes"] = ifs[4]  # tx_bytes
                            stats["net_rx_packets"] = ifs[1]  # rx_packets
                            stats["net_tx_packets"] = ifs[5]  # tx_packets
                            break
                    except Exception:
                        pass
    except Exception as e:
        log.debug("Interface stats error: %s", e)
    
    return stats


def _get_instance_stats_shell(instance_name: str) -> dict:
    """Fallback: get stats using virsh shell commands."""
    import subprocess
    stats = {}
    
    try:
        # CPU and memory via domstats
        result = subprocess.run(
            ["virsh", "-c", LIBVIRT_URI, "domstats", instance_name],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            for line in result.stdout.split("\n"):
                if "cpu.time=" in line:
                    val = int(line.split("=")[1])
                    stats["cpu_percent"] = min(100.0, val / 1e9 / SAMPLE_INTERVAL * 100)
                elif "balloon.rss=" in line:
                    stats["memory_used_mb"] = int(line.split("=")[1]) / 1024
                elif "balloon.current=" in line:
                    stats["memory_total_mb"] = int(line.split("=")[1]) / 1024
    except Exception as e:
        log.debug("Shell domstats error: %s", e)
    
    return stats


# ── Instance discovery ────────────────────────────────────────────────────────

def _get_active_instances() -> list[dict]:
    """Get list of active instances from compute-api or libvirt directly."""
    instances = []
    
    # Try compute-api first
    try:
        r = requests.get(
            f"{COMPUTE_API_URL}/servers?all_tenants=true&status=ACTIVE",
            headers=_api_headers(),
            timeout=10,
        )
        if r.status_code == 200:
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
        
        r = requests.post(
            f"{TELEMETRY_API_URL}/telemetry/metrics/batch",
            headers=_api_headers(),
            json=payload,
            timeout=30,
        )
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
                except Exception:
                    try:
                        dom = conn.lookupByName(instance_name)
                    except Exception:
                        dom = None
                
                if dom:
                    stats = _get_instance_stats_libvirt(dom)
            except Exception as e:
                log.debug("Error getting stats for %s: %s", instance_id, e)
        
        if not stats:
            # Fallback to shell
            stats = _get_instance_stats_shell(instance_name)
        
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


def main_loop():
    """Main worker loop."""
    log.info("Telemetry Worker started (interval=%ds, host=%s)", SAMPLE_INTERVAL, COMPUTE_HOST)
    
    while True:
        try:
            collect_and_send()
        except Exception as e:
            log.exception("Error in collection loop: %s", e)
        
        time.sleep(SAMPLE_INTERVAL)


if __name__ == "__main__":
    main_loop()
