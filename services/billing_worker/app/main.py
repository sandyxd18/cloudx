from __future__ import annotations
"""
cloudx Billing Worker — Usage Aggregation & Invoice Generator
===============================================================

Worker service (Fase 5.2) yang berjalan di background untuk:
  • Mengumpulkan data penggunaan resource dari service-service lain
  • Menghitung biaya berdasarkan Rating Sheet
  • Membuat invoice otomatis periode bulanan
  • Membersihkan usage records lama

Database: cloudx_billing (terisolasi dari service lain)
"""


import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

log = logging.getLogger("billing-worker")

# ── Config ────────────────────────────────────────────────────────────────────
BILLING_API_URL = os.getenv("BILLING_API_URL", "http://billing-api:8011").rstrip("/")
TELEMETRY_API_URL = os.getenv("TELEMETRY_API_URL", "http://telemetry-api:8010").rstrip("/")
COMPUTE_API_URL = os.getenv("COMPUTE_API_URL", "http://compute-api:8000").rstrip("/")
BLOCK_STORE_API_URL = os.getenv("BLOCK_STORE_API_URL", "http://block-store-api:8000").rstrip("/")
NETWORKING_API_URL = os.getenv("NETWORKING_API_URL", "http://networking-api:8000").rstrip("/")
AUTH_API_URL = os.getenv("AUTH_API_URL", "http://auth-api:8000").rstrip("/")
AUTH_ADMIN_USERNAME = os.getenv("AUTH_ADMIN_USERNAME", "admin")
AUTH_ADMIN_PASSWORD = os.getenv("AUTH_ADMIN_PASSWORD", "admin123")

AGGREGATION_INTERVAL = int(os.getenv("AGGREGATION_INTERVAL", "3600"))  # 1 hour
INVOICE_GENERATION_DAY = int(os.getenv("INVOICE_GENERATION_DAY", "1"))  # Day of month
CLEANUP_DAYS = int(os.getenv("CLEANUP_DAYS", "90"))

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


# ── Rating Sheet Cache ────────────────────────────────────────────────────────
_rating_cache: dict = {}
_rating_cache_time: datetime | None = None


def _get_rating_sheets() -> dict:
    """Get active rating sheets with caching."""
    global _rating_cache, _rating_cache_time
    
    now = datetime.now(timezone.utc)
    if _rating_cache_time and (now - _rating_cache_time).seconds < 300:
        return _rating_cache
    
    try:
        r = requests.get(
            f"{BILLING_API_URL}/billing/rating-sheets?active_only=true",
            headers=_api_headers(),
            timeout=10,
        )
        if r.status_code == 200:
            _rating_cache = {}
            for sheet in r.json():
                key = sheet["resource_type"]
                _rating_cache[key] = {
                    "rate": Decimal(str(sheet["rate_per_unit"])),
                    "unit": sheet["unit"],
                    "currency": sheet.get("currency", "IDR"),
                }
            _rating_cache_time = now
            return _rating_cache
    except Exception as e:
        log.error("Failed to fetch rating sheets: %s", e)
    
    return _rating_cache


# ── Resource Discovery ────────────────────────────────────────────────────────

def _get_active_servers() -> list[dict]:
    """Get all active servers from compute-api."""
    try:
        r = requests.get(
            f"{COMPUTE_API_URL}/servers?all_tenants=true",
            headers=_api_headers(),
            timeout=10,
        )
        if r.status_code == 200:
            return r.json().get("servers", [])
    except Exception as e:
        log.error("Failed to get servers: %s", e)
    return []


def _get_volumes() -> list[dict]:
    """Get all volumes from block-store-api."""
    try:
        r = requests.get(
            f"{BLOCK_STORE_API_URL}/volumes?all_tenants=true",
            headers=_api_headers(),
            timeout=10,
        )
        if r.status_code == 200:
            return r.json().get("volumes", [])
    except Exception as e:
        log.error("Failed to get volumes: %s", e)
    return []


def _get_floating_ips() -> list[dict]:
    """Get all floating IPs from networking-api."""
    try:
        r = requests.get(
            f"{NETWORKING_API_URL}/floatingips?all_tenants=true",
            headers=_api_headers(),
            timeout=10,
        )
        if r.status_code == 200:
            return r.json().get("floatingips", [])
    except Exception as e:
        log.error("Failed to get floating IPs: %s", e)
    return []


def _get_load_balancers() -> list[dict]:
    """Get all load balancers."""
    try:
        r = requests.get(
            f"{os.getenv('LOAD_BALANCER_API_URL', 'http://load-balancer-api:8008')}/loadbalancers?all_tenants=true",
            headers=_api_headers(),
            timeout=10,
        )
        if r.status_code == 200:
            return r.json().get("loadbalancers", [])
    except Exception as e:
        log.error("Failed to get load balancers: %s", e)
    return []


# ── Usage Aggregation ─────────────────────────────────────────────────────────

def _aggregate_server_usage(servers: list[dict], ratings: dict) -> list[dict]:
    """Calculate vCPU and RAM usage from active servers."""
    records = []
    period_start = datetime.now(timezone.utc) - timedelta(seconds=AGGREGATION_INTERVAL)
    period_end = datetime.now(timezone.utc)
    hours = Decimal(str(AGGREGATION_INTERVAL)) / Decimal("3600")
    
    vcpu_rate = ratings.get("vcpu", {}).get("rate", Decimal("0"))
    ram_rate = ratings.get("ram", {}).get("rate", Decimal("0"))
    
    for server in servers:
        if server.get("status") != "ACTIVE":
            continue
        
        project_id = server.get("project_id", server.get("tenant_id", "unknown"))
        flavor = server.get("flavor", {})
        vcpus = flavor.get("vcpus", 1)
        ram_mb = flavor.get("ram", 1024)
        ram_gb = Decimal(str(ram_mb)) / Decimal("1024")
        
        # vCPU usage
        if vcpu_rate > 0:
            records.append({
                "project_id": project_id,
                "resource_type": "vcpu",
                "resource_id": server.get("id", server.get("uuid")),
                "quantity": Decimal(str(vcpus)) * hours,
                "unit": "vcpu-hour",
                "rate_per_unit": vcpu_rate,
                "period_start": period_start.isoformat(),
                "period_end": period_end.isoformat(),
            })
        
        # RAM usage
        if ram_rate > 0:
            records.append({
                "project_id": project_id,
                "resource_type": "ram",
                "resource_id": server.get("id", server.get("uuid")),
                "quantity": ram_gb * hours,
                "unit": "gb-hour",
                "rate_per_unit": ram_rate,
                "period_start": period_start.isoformat(),
                "period_end": period_end.isoformat(),
            })
    
    return records


def _aggregate_volume_usage(volumes: list[dict], ratings: dict) -> list[dict]:
    """Calculate volume storage usage."""
    records = []
    period_start = datetime.now(timezone.utc) - timedelta(seconds=AGGREGATION_INTERVAL)
    period_end = datetime.now(timezone.utc)
    hours = Decimal(str(AGGREGATION_INTERVAL)) / Decimal("3600")
    
    volume_rate = ratings.get("volume", {}).get("rate", Decimal("0"))
    
    for volume in volumes:
        if volume.get("status") not in ("available", "in-use"):
            continue
        
        project_id = volume.get("project_id", volume.get("tenant_id", "unknown"))
        size_gb = volume.get("size", 0)
        
        if volume_rate > 0 and size_gb > 0:
            records.append({
                "project_id": project_id,
                "resource_type": "volume",
                "resource_id": volume.get("id"),
                "quantity": Decimal(str(size_gb)) * hours,
                "unit": "gb-hour",
                "rate_per_unit": volume_rate,
                "period_start": period_start.isoformat(),
                "period_end": period_end.isoformat(),
            })
    
    return records


def _aggregate_floating_ip_usage(fips: list[dict], ratings: dict) -> list[dict]:
    """Calculate floating IP usage."""
    records = []
    period_start = datetime.now(timezone.utc) - timedelta(seconds=AGGREGATION_INTERVAL)
    period_end = datetime.now(timezone.utc)
    hours = Decimal(str(AGGREGATION_INTERVAL)) / Decimal("3600")
    
    fip_rate = ratings.get("floating_ip", {}).get("rate", Decimal("0"))
    
    for fip in fips:
        if not fip.get("port_id"):
            continue  # Not allocated
        
        project_id = fip.get("project_id", fip.get("tenant_id", "unknown"))
        
        if fip_rate > 0:
            records.append({
                "project_id": project_id,
                "resource_type": "floating_ip",
                "resource_id": fip.get("id"),
                "quantity": hours,
                "unit": "ip-hour",
                "rate_per_unit": fip_rate,
                "period_start": period_start.isoformat(),
                "period_end": period_end.isoformat(),
            })
    
    return records


def _aggregate_load_balancer_usage(lbs: list[dict], ratings: dict) -> list[dict]:
    """Calculate load balancer usage."""
    records = []
    period_start = datetime.now(timezone.utc) - timedelta(seconds=AGGREGATION_INTERVAL)
    period_end = datetime.now(timezone.utc)
    hours = Decimal(str(AGGREGATION_INTERVAL)) / Decimal("3600")
    
    lb_rate = ratings.get("load_balancer", {}).get("rate", Decimal("0"))
    
    for lb in lbs:
        if lb.get("provisioning_status") != "ACTIVE":
            continue
        
        project_id = lb.get("project_id", lb.get("tenant_id", "unknown"))
        
        if lb_rate > 0:
            records.append({
                "project_id": project_id,
                "resource_type": "load_balancer",
                "resource_id": lb.get("id"),
                "quantity": hours,
                "unit": "lb-hour",
                "rate_per_unit": lb_rate,
                "period_start": period_start.isoformat(),
                "period_end": period_end.isoformat(),
            })
    
    return records


# ── Send usage records ────────────────────────────────────────────────────────

def _send_usage_records(records: list[dict]) -> bool:
    """Send usage records to billing-api."""
    if not records:
        return True
    
    try:
        r = requests.post(
            f"{BILLING_API_URL}/billing/usage-records/batch",
            headers=_api_headers(),
            json=records,
            timeout=30,
        )
        if r.status_code in (200, 201):
            log.info("Sent %d usage records", len(records))
            return True
        else:
            log.warning("Failed to send usage records: %s %s", r.status_code, r.text)
            return False
    except Exception as e:
        log.error("Error sending usage records: %s", e)
        return False


# ── Invoice generation ────────────────────────────────────────────────────────

def _generate_monthly_invoices():
    """Generate invoices for previous month on configured day."""
    now = datetime.now(timezone.utc)
    if now.day != INVOICE_GENERATION_DAY:
        return
    
    # Calculate previous month
    if now.month == 1:
        prev_year = now.year - 1
        prev_month = 12
    else:
        prev_year = now.year
        prev_month = now.month - 1
    
    log.info("Generating invoices for %d-%02d", prev_year, prev_month)
    
    # Get all projects from auth-api
    try:
        r = requests.get(
            f"{AUTH_API_URL}/auth/projects",
            headers=_api_headers(),
            timeout=10,
        )
        if r.status_code != 200:
            log.error("Failed to get projects: %s", r.text)
            return
        
        projects = r.json().get("projects", [])
        for project in projects:
            project_id = project.get("id")
            if not project_id:
                continue
            
            try:
                r = requests.post(
                    f"{BILLING_API_URL}/billing/invoices",
                    headers=_api_headers(),
                    params={"project_id": project_id, "year": prev_year, "month": prev_month},
                    timeout=30,
                )
                if r.status_code == 201:
                    log.info("Generated invoice for project %s", project_id)
                elif r.status_code == 409:
                    log.debug("Invoice already exists for project %s", project_id)
                else:
                    log.warning("Failed to generate invoice for %s: %s", project_id, r.text)
            except Exception as e:
                log.error("Error generating invoice for %s: %s", project_id, e)
    
    except Exception as e:
        log.error("Error in invoice generation: %s", e)


# ── Cleanup ───────────────────────────────────────────────────────────────────

def _cleanup_old_records():
    """Clean up usage records older than retention period."""
    try:
        r = requests.post(
            f"{BILLING_API_URL}/billing/cleanup?days={CLEANUP_DAYS}",
            headers=_api_headers(),
            timeout=30,
        )
        if r.status_code == 200:
            log.info("Cleaned up old records: %s", r.json())
    except Exception as e:
        log.error("Cleanup error: %s", e)


# ── Main loop ─────────────────────────────────────────────────────────────────

def aggregate_usage():
    """Main aggregation cycle."""
    ratings = _get_rating_sheets()
    if not ratings:
        log.warning("No rating sheets available, skipping aggregation")
        return
    
    all_records = []
    
    # Aggregate each resource type
    servers = _get_active_servers()
    all_records.extend(_aggregate_server_usage(servers, ratings))
    
    volumes = _get_volumes()
    all_records.extend(_aggregate_volume_usage(volumes, ratings))
    
    fips = _get_floating_ips()
    all_records.extend(_aggregate_floating_ip_usage(fips, ratings))
    
    lbs = _get_load_balancers()
    all_records.extend(_aggregate_load_balancer_usage(lbs, ratings))
    
    if all_records:
        _send_usage_records(all_records)
        log.info("Aggregated %d usage records", len(all_records))


def main_loop():
    """Main worker loop."""
    log.info("Billing Worker started (aggregation_interval=%ds, invoice_day=%d)",
             AGGREGATION_INTERVAL, INVOICE_GENERATION_DAY)
    
    last_invoice_check = 0
    last_cleanup = 0
    
    while True:
        try:
            # Aggregate usage
            aggregate_usage()
            
            # Check for invoice generation (once per day)
            now = time.time()
            if now - last_invoice_check > 86400:
                _generate_monthly_invoices()
                last_invoice_check = now
            
            # Cleanup (once per day)
            if now - last_cleanup > 86400:
                _cleanup_old_records()
                last_cleanup = now
        
        except Exception as e:
            log.exception("Error in main loop: %s", e)
        
        time.sleep(AGGREGATION_INTERVAL)


if __name__ == "__main__":
    main_loop()
