"""
cloudx Network Reconciler (networking-api add-on)
=================================================

Runs periodically to compare the networking DB against the OVN Northbound DB
and corrects drift.

Drift scenarios this fixes:
  • Logical switch/router/port deleted outside of our handlers (host reboot,
    manual cleanup, OVN crash) → mark resource in DB as error.
  • Network/subnet/port stuck in "deleting" but OVN object already gone
    → hard-delete DB record.

Scheduling: background thread (daemon) started in networking-api startup,
sleeping RECONCILE_INTERVAL_SECONDS between passes. Default = 3600 (1 hour).
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import time

import requests

log = logging.getLogger("network-reconciler")

RECONCILE_INTERVAL = int(os.getenv("RECONCILE_INTERVAL_SECONDS", "60"))
OVN_NB_DB = os.getenv("OVN_NB_DB", "tcp:ovn-nb-db:6641")
NETWORKING_API_URL = os.getenv("NETWORKING_API_URL", "http://127.0.0.1:8001")


def _ovn_find(table: str, columns: str = "_uuid,name") -> list[dict]:
    """Find all rows in an OVN table. Returns list of dicts."""
    try:
        cmd = ["ovn-nbctl", f"--db={OVN_NB_DB}", "--timeout=8",
               "--format=json", f"--columns={columns}", "find", table]
        proc = subprocess.run(cmd, text=True, capture_output=True, timeout=15)
        if proc.returncode != 0:
            log.warning("ovn-nbctl find %s failed: %s", table, proc.stderr.strip())
            return []
        # Parse JSON output
        import json
        data = json.loads(proc.stdout)
        return data.get("data", [])
    except Exception as exc:
        log.warning("ovn-nbctl find %s error: %s", table, exc)
        return []


def _ovn_exists(table: str, name: str) -> bool:
    """Check if an OVN object by name exists."""
    try:
        cmd = ["ovn-nbctl", f"--db={OVN_NB_DB}", "--timeout=8",
               "--format=list", "--columns=name", "find", table, f'name="{name}"']
        proc = subprocess.run(cmd, text=True, capture_output=True, timeout=15)
        return proc.returncode == 0 and name in proc.stdout
    except Exception:
        return False


def _patch_network_status(network_id: str, status: str) -> None:
    """Patch network status via networking-api HTTP endpoint."""
    try:
        resp = requests.patch(
            f"{NETWORKING_API_URL}/networks/{network_id}",
            json={"status": status},
            timeout=10,
        )
        if resp.status_code not in (200, 204):
            log.warning("Failed to patch network %s: HTTP %s", network_id, resp.status_code)
    except Exception as exc:
        log.warning("Failed to patch network %s: %s", network_id, exc)


def _delete_network_record(network_id: str) -> None:
    """Hard-delete network record via networking-api HTTP endpoint."""
    try:
        resp = requests.delete(
            f"{NETWORKING_API_URL}/networks/{network_id}",
            timeout=10,
        )
        if resp.status_code not in (200, 204, 404):
            log.warning("Failed to delete network %s: HTTP %s", network_id, resp.status_code)
    except Exception as exc:
        log.warning("Failed to delete network %s: %s", network_id, exc)


def _patch_subnet_status(subnet_id: str, status: str) -> None:
    """Patch subnet status via networking-api HTTP endpoint."""
    try:
        resp = requests.patch(
            f"{NETWORKING_API_URL}/subnets/{subnet_id}",
            json={"status": status},
            timeout=10,
        )
        if resp.status_code not in (200, 204):
            log.warning("Failed to patch subnet %s: HTTP %s", subnet_id, resp.status_code)
    except Exception as exc:
        log.warning("Failed to patch subnet %s: %s", subnet_id, exc)


def _delete_subnet_record(subnet_id: str) -> None:
    """Hard-delete subnet record via networking-api HTTP endpoint."""
    try:
        resp = requests.delete(
            f"{NETWORKING_API_URL}/subnets/{subnet_id}",
            timeout=10,
        )
        if resp.status_code not in (200, 204, 404):
            log.warning("Failed to delete subnet %s: HTTP %s", subnet_id, resp.status_code)
    except Exception as exc:
        log.warning("Failed to delete subnet %s: %s", subnet_id, exc)


def _patch_router_status(router_id: str, status: str) -> None:
    """Patch router status via networking-api HTTP endpoint."""
    try:
        resp = requests.patch(
            f"{NETWORKING_API_URL}/routers/{router_id}",
            json={"status": status},
            timeout=10,
        )
        if resp.status_code not in (200, 204):
            log.warning("Failed to patch router %s: HTTP %s", router_id, resp.status_code)
    except Exception as exc:
        log.warning("Failed to patch router %s: %s", router_id, exc)


def _delete_router_record(router_id: str) -> None:
    """Hard-delete router record via networking-api HTTP endpoint."""
    try:
        resp = requests.delete(
            f"{NETWORKING_API_URL}/routers/{router_id}",
            timeout=10,
        )
        if resp.status_code not in (200, 204, 404):
            log.warning("Failed to delete router %s: HTTP %s", router_id, resp.status_code)
    except Exception as exc:
        log.warning("Failed to delete router %s: %s", router_id, exc)


# ═══════════════════════════════════════════════════════════════════════════════
# Reconciliation passes
# ═══════════════════════════════════════════════════════════════════════════════

def reconcile_networks() -> None:
    """One pass: compare all DB network records against OVN logical_switches."""
    try:
        resp = requests.get(
            f"{NETWORKING_API_URL}/networks",
            params={"limit": 10000},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.warning("[reconcile] could not fetch network list: %s", exc)
        return

    networks = data.get("networks", [])
    if not networks:
        return

    for net in networks:
        db_status = net.get("status", "").upper()
        net_name = net.get("name")
        if not net_name:
            continue

        if db_status not in ("ACTIVE", "ERROR", "DELETING"):
            continue

        ovn_exists = _ovn_exists("Logical_Switch", net_name)

        if not ovn_exists and db_status == "ACTIVE":
            log.warning(
                "[reconcile] network %s (%s): status=ACTIVE but OVN ls missing → marking error",
                net_name, net["id"],
            )
            _patch_network_status(net["id"], "error")
            continue

        if not ovn_exists and db_status == "DELETING":
            log.info(
                "[reconcile] network %s (%s): deleting and OVN ls gone → removing DB record",
                net_name, net["id"],
            )
            _delete_network_record(net["id"])
            continue

        if ovn_exists and db_status == "ERROR":
            log.info(
                "[reconcile] network %s (%s): status=error but OVN ls exists → recovering to ACTIVE",
                net_name, net["id"],
            )
            _patch_network_status(net["id"], "ACTIVE")


def reconcile_subnets() -> None:
    """One pass: compare all DB subnet records against OVN DHCP_Options."""
    try:
        resp = requests.get(
            f"{NETWORKING_API_URL}/subnets",
            params={"limit": 10000},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.warning("[reconcile] could not fetch subnet list: %s", exc)
        return

    subnets = data.get("subnets", [])
    if not subnets:
        return

    for subnet in subnets:
        db_status = subnet.get("status", "").upper()
        dhcp_uuid = subnet.get("ovn_dhcp_options_uuid") or subnet.get("dhcp_options_id")
        if not dhcp_uuid:
            continue

        if db_status not in ("ACTIVE", "ERROR", "DELETING"):
            continue

        # Check if DHCP_Options UUID exists in OVN
        ovn_exists = _ovn_exists("DHCP_Options", dhcp_uuid)

        if not ovn_exists and db_status == "ACTIVE":
            log.warning(
                "[reconcile] subnet %s (%s): status=ACTIVE but OVN DHCP missing → marking error",
                subnet.get("name"), subnet["id"],
            )
            _patch_subnet_status(subnet["id"], "error")
            continue

        if not ovn_exists and db_status == "DELETING":
            log.info(
                "[reconcile] subnet %s (%s): deleting and OVN DHCP gone → removing DB record",
                subnet.get("name"), subnet["id"],
            )
            _delete_subnet_record(subnet["id"])
            continue

        if ovn_exists and db_status == "ERROR":
            log.info(
                "[reconcile] subnet %s (%s): status=error but OVN DHCP exists → recovering to ACTIVE",
                subnet.get("name"), subnet["id"],
            )
            _patch_subnet_status(subnet["id"], "ACTIVE")


def reconcile_routers() -> None:
    """One pass: compare all DB router records against OVN logical_routers."""
    try:
        resp = requests.get(
            f"{NETWORKING_API_URL}/routers",
            params={"limit": 10000},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.warning("[reconcile] could not fetch router list: %s", exc)
        return

    routers = data.get("routers", [])
    if not routers:
        return

    for router in routers:
        db_status = router.get("status", "").upper()
        router_name = router.get("name")
        if not router_name:
            continue

        if db_status not in ("ACTIVE", "ERROR", "DELETING"):
            continue

        ovn_exists = _ovn_exists("Logical_Router", router_name)

        if not ovn_exists and db_status == "ACTIVE":
            log.warning(
                "[reconcile] router %s (%s): status=ACTIVE but OVN lr missing → marking error",
                router_name, router["id"],
            )
            _patch_router_status(router["id"], "error")
            continue

        if not ovn_exists and db_status == "DELETING":
            log.info(
                "[reconcile] router %s (%s): deleting and OVN lr gone → removing DB record",
                router_name, router["id"],
            )
            _delete_router_record(router["id"])
            continue

        if ovn_exists and db_status == "ERROR":
            log.info(
                "[reconcile] router %s (%s): status=error but OVN lr exists → recovering to ACTIVE",
                router_name, router["id"],
            )
            _patch_router_status(router["id"], "ACTIVE")


# ═══════════════════════════════════════════════════════════════════════════════
# Daemon loop
# ═══════════════════════════════════════════════════════════════════════════════

def reconcile_loop() -> None:
    """Background thread — runs reconciliation every RECONCILE_INTERVAL seconds."""
    log.info(
        "[reconcile] network reconciliation loop started (interval=%ds)",
        RECONCILE_INTERVAL,
    )
    while True:
        time.sleep(RECONCILE_INTERVAL)
        reconcile_networks()
        reconcile_subnets()
        reconcile_routers()


def start_reconcile_thread() -> threading.Thread:
    """Start the background network reconciler daemon thread."""
    t = threading.Thread(target=reconcile_loop, daemon=True)
    t.start()
    return t
