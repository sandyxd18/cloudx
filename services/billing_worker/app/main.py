from __future__ import annotations
from shared.config import load_service_config
load_service_config("billing-worker")
"""
cloudx Billing Worker — Usage Aggregation & Invoice Generator
===============================================================

Background worker service (Phase 5.2) that:
  • Collects resource usage data from the other services
  • Sends it to billing-api, which prices it from the Rating Sheet and dedupes
  • Generates automatic monthly invoices
  • Cleans up old usage records

Database: cloudx_billing (isolated from the other services)
"""


import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import requests
from shared import catalog
from shared.internal import internal_headers


log = logging.getLogger("billing-worker")

# ── Config ────────────────────────────────────────────────────────────────────
AUTH_API_URL = os.getenv("AUTH_API_URL", "http://auth-api:8000").rstrip("/")

AGGREGATION_INTERVAL = int(os.getenv("AGGREGATION_INTERVAL", "3600"))  # 1 hour
INVOICE_GENERATION_DAY = int(os.getenv("INVOICE_GENERATION_DAY", "1"))  # Day of month
CLEANUP_DAYS = int(os.getenv("CLEANUP_DAYS", "90"))
STATE_FILE = os.getenv("BILLING_STATE_FILE", "/var/lib/cloudx-billing/seen.json")

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s [%(name)s] %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

def _billing_api() -> str:
    return catalog.url("billing").rstrip("/")


def _request(method: str, url: str, **kw) -> requests.Response:
    headers = {**kw.pop("headers", {}), "Content-Type": "application/json", **internal_headers()}
    return requests.request(method, url, headers=headers, **kw)


# ── Time math ─────────────────────────────────────────────────────────────────

def _dt(v) -> datetime | None:
    if not v:
        return None
    if isinstance(v, str):
        v = datetime.fromisoformat(v.replace("Z", "+00:00"))
    return v if v.tzinfo else v.replace(tzinfo=timezone.utc)  # services emit naive UTC


def overlap_hours(created, deleted, start, end) -> Decimal:
    """Hours a resource (created..deleted; None = unknown/still alive) existed inside [start, end]."""
    s, e, c, d = _dt(start), _dt(end), _dt(created), _dt(deleted)
    lo = max(s, c) if c else s
    hi = min(e, d) if d else e
    secs = (hi - lo).total_seconds()
    return Decimal(str(secs)) / Decimal("3600") if secs > 0 else Decimal("0")


# ── Resource Discovery ────────────────────────────────────────────────────────

def _list(service: str, path: str, key: str) -> list[dict] | None:
    try:
        url = f"{catalog.url(service).rstrip('/')}{path}"
        r = _request("GET", url, timeout=10)
        if r.status_code in (200, 201):
            data = r.json()
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return data.get(key) or data.get('items') or data.get(key.replace('-', '_')) or []
        log.error("GET %s -> %s", url, r.status_code)
    except Exception as e:
        log.error("Failed to get %s: %s", key, e)
    return None


def _get_active_servers() -> list[dict] | None:
    return _list("compute", "/servers?all_tenants=true", "servers")


def _get_volumes() -> list[dict] | None:
    return _list("volume", "/volumes?all_tenants=true", "volumes")


def _get_floating_ips() -> list[dict] | None:
    return _list("network", "/floating-ips?all_tenants=true", "floatingips")


def _get_load_balancers() -> list[dict] | None:
    return _list("load-balancer", "/load-balancers?all_tenants=true", "loadbalancers")


# ── Usage Aggregation ─────────────────────────────────────────────────────────

def _rec(res: dict, rtype: str, per_hour, start, end, name=None) -> dict | None:
    """Usage record for res over [start, end] (quantity = per_hour * overlap hours), None if no overlap."""
    qty = Decimal(str(per_hour)) * overlap_hours(res.get("created_at"), res.get("deleted_at"), start, end)
    if qty <= 0:
        return None
    return {
        "project_id": res.get("project_id") or res.get("tenant_id") or res.get("os-vol-tenant-attr:tenant_id") or "unknown",
        "resource_type": rtype,
        "resource_id": res.get("id") or res.get("uuid"),
        "resource_name": name or res.get("name"),
        "quantity": str(qty.quantize(Decimal("0.0001"))),
        "unit": "hour",
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
    }


def _keep(recs) -> list[dict]:
    return [r for r in recs if r]


def _aggregate_server_usage(servers: list[dict], start, end) -> list[dict]:
    out = []
    for s in servers:
        if s.get("status") != "ACTIVE":
            continue
        flavor = s.get("flavor") if isinstance(s.get("flavor"), dict) else {}
        vcpus = s.get("vcpus") or flavor.get("vcpus") or 1
        ram_mb = s.get("ram_mb") or flavor.get("ram") or 1024
        out += _keep([
            _rec(s, "vcpu", vcpus, start, end),
            _rec(s, "ram", Decimal(str(ram_mb)) / 1024, start, end),
        ])
    return out


def _aggregate_volume_usage(volumes: list[dict], start, end) -> list[dict]:
    return _keep(
        _rec(v, "volume", v.get("size_gb") or v.get("size") or 0, start, end)
        for v in volumes if v.get("status") in ("available", "in-use")
    )


def _aggregate_floating_ip_usage(fips: list[dict], start, end) -> list[dict]:
    return _keep(_rec(f, "floating_ip", 1, start, end, f.get("floating_ip_address")) for f in fips if f.get("port_id"))


def _aggregate_load_balancer_usage(lbs: list[dict], start, end) -> list[dict]:
    recs = []
    for lb in lbs:
        if lb.get("provisioning_status") != "ACTIVE":
            continue
        lb_type = str(lb.get("lb_type") or "").lower()
        rtype = f"load_balancer_{lb_type}" if lb_type in ("nlb", "alb") else "load_balancer"
        r = _rec(lb, rtype, 1, start, end)
        if r:
            recs.append(r)
    return recs


# ── Send usage records ────────────────────────────────────────────────────────

def _send_usage_records(records: list[dict]) -> bool:
    """Send usage records to billing-api (it rates and dedupes them)."""
    if not records:
        return True
    try:
        r = _request("POST", f"{_billing_api()}/billing/usage-records/batch", json=records, timeout=30)
        if r.status_code in (200, 201):
            res = r.json()
            log.info("Usage records: %s created, skipped %s", res.get("created"), res.get("skipped"))
            return True
        log.warning("Failed to send usage records: %s %s", r.status_code, r.text)
    except Exception as e:
        log.error("Error sending usage records: %s", e)
    return False


# ── Invoice generation ────────────────────────────────────────────────────────

def _generate_monthly_invoices():
    """Generate invoices for previous month on configured day."""
    now = datetime.now(timezone.utc)
    if now.day != INVOICE_GENERATION_DAY:
        return

    prev_year, prev_month = (now.year - 1, 12) if now.month == 1 else (now.year, now.month - 1)
    log.info("Generating invoices for %d-%02d", prev_year, prev_month)

    try:
        billing = _billing_api()
        r = _request("GET", f"{AUTH_API_URL}/auth/projects", timeout=10)
        if r.status_code != 200:
            log.error("Failed to get projects: %s", r.text)
            return

        for project in r.json().get("projects", []):
            project_id = project.get("id")
            if not project_id:
                continue
            try:
                r = _request(
                    "POST", f"{billing}/billing/invoices",
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
        r = _request("POST", f"{_billing_api()}/billing/cleanup?days={CLEANUP_DAYS}", timeout=30)
        if r.status_code in (200, 201):
            log.info("Cleaned up old records: %s", r.json())
    except Exception as e:
        log.error("Cleanup error: %s", e)


# ── Main loop ─────────────────────────────────────────────────────────────────

def _load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE + ".tmp", "w") as f:
            json.dump(state, f)
        os.replace(STATE_FILE + ".tmp", STATE_FILE)
    except OSError as e:
        log.warning("Cannot save state %s: %s", STATE_FILE, e)


def _lookup_deleted_at(resource_id: str) -> str | None:
    """Query event-api for deletion event timestamp of a vanished resource."""
    try:
        r = _request("GET", f"{catalog.url('event').rstrip('/')}/events/logs", params={"resource_id": resource_id, "limit": 10}, timeout=5)
        if r.status_code == 200:
            events = r.json()
            if isinstance(events, list):
                del_events = [e for e in events if "delete" in str(e.get("event_type", "")).lower()]
                if del_events:
                    return del_events[0].get("timestamp")
    except Exception as e:
        log.warning("Failed to lookup delete event for %s: %s", resource_id, e)
    return None


def _event_api_resolvable(old: dict, now: dict) -> bool:
    """Once per run, so vanished resources do not each wait for a failing catalog; they fall back to the estimate."""
    if all(k in now for k in old):
        return True
    try:
        catalog.url("event")
    except catalog.CatalogError as e:
        log.warning("Cannot look up delete events, estimating deletion times: %s", e)
        return False
    return True


def _with_ghosts(kind: str, listed: list[dict] | None, state: dict, start, end) -> list[dict]:
    """Add resources seen last run but gone now (deleted). Uses exact event deletion timestamp if available,
    falling back to half-interval estimate if no delete event is found.
    A failed listing (None) keeps the old snapshot so nothing is billed as deleted by mistake."""
    if listed is None:
        return []
    old = state.get(kind, {})
    now = {str(r.get("id") or r.get("uuid")): r for r in listed}
    died = start + (end - start) / 2
    state[kind] = now
    ghosts = []
    lookup = _lookup_deleted_at if _event_api_resolvable(old, now) else (lambda _id: None)
    for k, r in old.items():
        if k not in now:
            del_at = lookup(k) or died.isoformat()
            ghosts.append({**r, "deleted_at": del_at})
    return listed + ghosts


def aggregate_usage():
    """Bill the last complete interval. Boundaries are floored so a re-run yields identical (deduped) periods."""
    t = int(time.time()) // AGGREGATION_INTERVAL * AGGREGATION_INTERVAL
    end = datetime.fromtimestamp(t, timezone.utc)
    start = end - timedelta(seconds=AGGREGATION_INTERVAL)

    state = _load_state()
    servers = _with_ghosts("servers", _get_active_servers(), state, start, end)
    volumes = _with_ghosts("volumes", _get_volumes(), state, start, end)
    fips = _with_ghosts("fips", _get_floating_ips(), state, start, end)
    lbs = _with_ghosts("lbs", _get_load_balancers(), state, start, end)
    records = (
        _aggregate_server_usage(servers, start, end)
        + _aggregate_volume_usage(volumes, start, end)
        + _aggregate_floating_ip_usage(fips, start, end)
        + _aggregate_load_balancer_usage(lbs, start, end)
    )
    if _send_usage_records(records):
        _save_state(state)

def main_loop():
    """Main worker loop."""
    log.info("Billing Worker started (aggregation_interval=%ds, invoice_day=%d)",
             AGGREGATION_INTERVAL, INVOICE_GENERATION_DAY)

    last_invoice_check = 0
    last_cleanup = 0

    while True:
        try:
            aggregate_usage()

            now = time.time()
            if now - last_invoice_check > 86400:
                _generate_monthly_invoices()
                last_invoice_check = now

            if now - last_cleanup > 86400:
                _cleanup_old_records()
                last_cleanup = now

        except Exception as e:
            log.exception("Error in main loop: %s", e)

        time.sleep(AGGREGATION_INTERVAL)


if __name__ == "__main__":
    main_loop()
