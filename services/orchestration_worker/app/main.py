from __future__ import annotations
"""
cloudx Orchestration Worker — Auto-Scaling Group Monitor
=========================================================

Background worker service (Phase 6.2) that:
  • Monitors the average CPU of instances in a scaling group
  • Executes scale-out (spawn a VM + register it in the LB pool)
  • Executes scale-in (drain + terminate a VM)
  • Maintains the cooldown hysteresis

Database: cloudx_orchestration
"""


import logging
import os
import time
from datetime import datetime, timedelta, timezone

import requests


log = logging.getLogger("orchestration-worker")

# ── Config ────────────────────────────────────────────────────────────────────
ORCHESTRATION_API_URL = os.getenv("ORCHESTRATION_API_URL", "http://orchestration-api:8013").rstrip("/")
COMPUTE_API_URL = os.getenv("COMPUTE_API_URL", "http://compute-api:8000").rstrip("/")
TELEMETRY_API_URL = os.getenv("TELEMETRY_API_URL", "http://telemetry-api:8010").rstrip("/")
LOAD_BALANCER_API_URL = os.getenv("LOAD_BALANCER_API_URL", "http://load-balancer-api:8008").rstrip("/")
AUTH_API_URL = os.getenv("AUTH_API_URL", "http://auth-api:8000").rstrip("/")
AUTH_ADMIN_USERNAME = os.getenv("AUTH_ADMIN_USERNAME", "admin")
AUTH_ADMIN_PASSWORD = os.environ["AUTH_ADMIN_PASSWORD"]

MONITOR_INTERVAL = int(os.getenv("MONITOR_INTERVAL", "30"))  # seconds

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


# ── API Helpers ───────────────────────────────────────────────────────────────

def _get_scaling_groups() -> list[dict]:
    """Get all active scaling groups."""
    try:
        r = requests.get(
            f"{ORCHESTRATION_API_URL}/scaling-groups?active_only=true",
            headers=_api_headers(),
            timeout=10,
        )
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        log.error("Failed to get scaling groups: %s", e)
    return []


def _get_group_instances(group_id: str) -> list[dict]:
    """Get instances in a scaling group."""
    try:
        r = requests.get(
            f"{ORCHESTRATION_API_URL}/scaling-groups/{group_id}/instances",
            headers=_api_headers(),
            timeout=10,
        )
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        log.error("Failed to get group instances: %s", e)
    return []


def _get_server(server_id: str) -> dict | None:
    """Get server details from compute-api."""
    try:
        r = requests.get(
            f"{COMPUTE_API_URL}/servers/{server_id}",
            headers=_api_headers(),
            timeout=10,
        )
        if r.status_code == 200:
            return r.json().get("server", r.json())
    except Exception as e:
        log.error("Failed to get server %s: %s", server_id, e)
    return None


def _get_instance_avg_cpu(server_ids: list[str]) -> float | None:
    """Get average CPU across instances from telemetry-api."""
    if not server_ids:
        return None
    
    total_cpu = 0.0
    count = 0
    from_time = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    
    for server_id in server_ids:
        try:
            r = requests.get(
                f"{TELEMETRY_API_URL}/telemetry/metrics?instance_id={server_id}&metric_type=cpu_percent&from_time={from_time}&limit=10",
                headers=_api_headers(),
                timeout=10,
            )
            if r.status_code == 200:
                metrics = r.json()
                if metrics:
                    # Take the latest metric
                    total_cpu += metrics[0]["value"]
                    count += 1
        except Exception as e:
            log.debug("Failed to get CPU for %s: %s", server_id, e)
    
    if count == 0:
        return None
    return total_cpu / count


def _spawn_server(group: dict, name: str) -> dict | None:
    """Spawn a new server via compute-api."""
    payload = {
        "name": name,
        "flavor": group["flavor_id"],
        "image": group["image_id"],
        "network": group["network_id"],
    }
    
    try:
        r = requests.post(
            f"{COMPUTE_API_URL}/servers",
            headers=_api_headers(),
            json=payload,
            timeout=30,
        )
        if r.status_code in (200, 201, 202):
            return r.json()
        else:
            log.error("Failed to spawn server: %s %s", r.status_code, r.text)
    except Exception as e:
        log.error("Error spawning server: %s", e)
    return None


def _terminate_server(server_id: str) -> bool:
    """Terminate a server via compute-api."""
    try:
        r = requests.delete(
            f"{COMPUTE_API_URL}/servers/{server_id}",
            headers=_api_headers(),
            timeout=30,
        )
        return r.status_code in (200, 202, 204)
    except Exception as e:
        log.error("Error terminating server %s: %s", server_id, e)
    return False


def _add_member_to_pool(pool_id: str, server: dict) -> bool:
    """Add server to load balancer pool."""
    if not pool_id:
        return True
    
    ip_address = server.get("ip_address")
    if not ip_address:
        log.warning("No IP address found for server %s", server.get("id"))
        return False
    
    try:
        r = requests.post(
            f"{LOAD_BALANCER_API_URL}/pools/{pool_id}/members",
            headers=_api_headers(),
            json={
                "address": ip_address,
                "protocol_port": 80,
                "weight": 1,
                "instance_id": server.get("id"),
            },
            timeout=30,
        )
        return r.status_code in (200, 201, 202)
    except Exception as e:
        log.error("Error adding member to pool: %s", e)
    return False


def _remove_member_from_pool(pool_id: str, server_id: str) -> bool:
    """Remove server from load balancer pool."""
    if not pool_id:
        return True
    
    try:
        r = requests.delete(
            f"{LOAD_BALANCER_API_URL}/pools/{pool_id}/members/{server_id}",
            headers=_api_headers(),
            timeout=30,
        )
        return r.status_code in (200, 202, 204)
    except Exception as e:
        log.error("Error removing member from pool: %s", e)
    return False


# ── Scaling Logic ─────────────────────────────────────────────────────────────

def _record_history(
    group_id: str,
    action: str,
    reason: str,
    server_id: str | None,
    avg_cpu_before: float | None,
    instance_count_before: int,
    instance_count_after: int,
    server_name: str | None = None,
):
    """Record scaling history via orchestration-api."""
    try:
        requests.post(
            f"{ORCHESTRATION_API_URL}/scaling-groups/{group_id}/history",
            headers=_api_headers(),
            json={
                "action": action,
                "reason": reason,
                "server_id": server_id,
                "server_name": server_name,
                "avg_cpu_before": avg_cpu_before,
                "instance_count_before": instance_count_before,
                "instance_count_after": instance_count_after,
            },
            timeout=10,
        )
    except Exception as e:
        log.debug("Failed to record history: %s", e)


def _check_group(group: dict):
    """Evaluate and execute scaling for a single group."""
    group_id = group["id"]
    instances = _get_group_instances(group_id)
    active_instances = [i for i in instances if i.get("status") == "active"]
    instance_count = len(active_instances)
    
    now = datetime.now(timezone.utc)
    
    # Check cooldowns
    last_scale_out = group.get("last_scale_out_at")
    last_scale_in = group.get("last_scale_in_at")
    
    # Get average CPU
    server_ids = [i["server_id"] for i in active_instances]
    avg_cpu = _get_instance_avg_cpu(server_ids)
    
    log.debug("Group %s: %d instances, avg CPU %s", group_id, instance_count, avg_cpu)
    
    if avg_cpu is None:
        return
    
    # Scale-out check
    if avg_cpu > group["target_cpu_percent"]:
        if last_scale_out:
            last = datetime.fromisoformat(last_scale_out.replace("Z", "+00:00"))
            if (now - last).total_seconds() < group["cooldown_seconds"]:
                log.debug("Scale-out cooldown active for group %s", group_id)
                return
        
        if instance_count >= group["max_instances"]:
            log.info("Group %s already at max instances (%d)", group_id, group["max_instances"])
            return
        
        # Scale out by 1 (or more if needed)
        new_count = min(instance_count + 1, group["max_instances"])
        to_add = new_count - instance_count
        
        log.info("Scaling out group %s: %d -> %d (avg CPU %.1f%%)",
                 group_id, instance_count, new_count, avg_cpu)
        
        for i in range(to_add):
            server_name = f"{group['name']}-asg-{int(time.time())}-{i}"
            server = _spawn_server(group, server_name)
            if server:
                server_id = server.get("id") or server.get("uuid") or server.get("server", {}).get("id")
                if server_id and group.get("load_balancer_pool_id"):
                    server_detail = _get_server(server_id)
                    if server_detail:
                        _add_member_to_pool(group["load_balancer_pool_id"], server_detail)
                
                _record_history(
                    group_id, "scale_out",
                    f"High CPU usage: {avg_cpu:.1f}%",
                    server_id, avg_cpu,
                    instance_count + i, instance_count + i + 1,
                    server_name,
                )
        
        return
    
    # Scale-in check (CPU below 50% of target)
    scale_in_threshold = group["target_cpu_percent"] * 0.5
    if avg_cpu < scale_in_threshold and instance_count > group["min_instances"]:
        if last_scale_in:
            last = datetime.fromisoformat(last_scale_in.replace("Z", "+00:00"))
            if (now - last).total_seconds() < group["scale_in_cooldown_seconds"]:
                log.debug("Scale-in cooldown active for group %s", group_id)
                return
        
        # Scale in by 1, remove newest instance
        new_count = max(instance_count - 1, group["min_instances"])
        to_remove = instance_count - new_count
        
        # Sort by created_at descending
        sorted_instances = sorted(
            active_instances,
            key=lambda x: x.get("created_at", ""),
            reverse=True,
        )
        
        log.info("Scaling in group %s: %d -> %d (avg CPU %.1f%%)",
                 group_id, instance_count, new_count, avg_cpu)
        
        for i in range(to_remove):
            if i >= len(sorted_instances):
                break
            inst = sorted_instances[i]
            server_id = inst["server_id"]
            
            # Remove from LB pool first (drain)
            if group.get("load_balancer_pool_id"):
                _remove_member_from_pool(group["load_balancer_pool_id"], server_id)
            
            # Terminate server
            _terminate_server(server_id)
            
            _record_history(
                group_id, "scale_in",
                f"Low CPU usage: {avg_cpu:.1f}%",
                server_id, avg_cpu,
                instance_count - i, instance_count - i - 1,
            )


def main_loop():
    """Main monitoring loop."""
    log.info("Orchestration Worker started (monitor_interval=%ds)", MONITOR_INTERVAL)
    
    while True:
        try:
            groups = _get_scaling_groups()
            for group in groups:
                try:
                    _check_group(group)
                except Exception as e:
                    log.exception("Error checking group %s: %s", group.get("id"), e)
        except Exception as e:
            log.exception("Error in monitoring loop: %s", e)
        
        time.sleep(MONITOR_INTERVAL)


if __name__ == "__main__":
    main_loop()
