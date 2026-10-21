from __future__ import annotations
from shared.config import load_service_config
load_service_config("event-worker")
"""
cloudx Event Worker — Webhook Delivery Engine
==============================================

Background worker service (Phase 5.3) that:
  • Fetches pending events from the database
  • Sends an HTTP POST to the webhook endpoint with an HMAC signature
  • Manages retries with exponential backoff
  • Handles the dead letter queue for failed events

Database: cloudx_events
"""


import hashlib
import hmac
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone

import requests
from shared import catalog
from shared.internal import internal_headers


log = logging.getLogger("event-worker")

# ── Config ────────────────────────────────────────────────────────────────────
AUTH_API_URL = os.getenv("AUTH_API_URL", "http://auth-api:8000").rstrip("/")

POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "10"))  # seconds
WEBHOOK_TIMEOUT = int(os.getenv("WEBHOOK_TIMEOUT", "10"))
MAX_RETRY_ATTEMPTS = int(os.getenv("MAX_RETRY_ATTEMPTS", "5"))
BASE_RETRY_DELAY = int(os.getenv("BASE_RETRY_DELAY", "60"))  # seconds

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s [%(name)s] %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


# ── Webhook Delivery ──────────────────────────────────────────────────────────

def backoff_seconds(attempt: int) -> int:
    """Exponential backoff: BASE * 2^(attempt-1), capped at 1 hour. attempt is 1-based."""
    return min(BASE_RETRY_DELAY * 2 ** max(attempt - 1, 0), 3600)


def _sign_payload(secret: str, payload: dict) -> str:
    """Generate HMAC-SHA256 signature."""
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    signature = hmac.new(
        secret.encode("utf-8"),
        body.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"sha256={signature}"


def _deliver_webhook(endpoint_url: str, secret: str, event_type: str, event_id: str, payload: dict) -> tuple[int, str]:
    """Send webhook HTTP POST and return (status_code, response_body)."""
    full_payload = {
        "event_type": event_type,
        "event_id": event_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
    }
    
    headers = {
        "Content-Type": "application/json",
        "X-CloudX-Event-ID": event_id,
        "X-CloudX-Event-Type": event_type,
        "X-CloudX-Signature": _sign_payload(secret, full_payload),
        "User-Agent": "CloudX-Event-Worker/1.0",
    }
    
    try:
        r = requests.post(
            endpoint_url,
            headers=headers,
            json=full_payload,
            timeout=WEBHOOK_TIMEOUT,
        )
        return r.status_code, r.text[:1000]  # Limit response body
    except requests.exceptions.Timeout:
        return -1, "Timeout"
    except requests.exceptions.ConnectionError:
        return -2, "Connection Error"
    except Exception as e:
        return -3, str(e)[:1000]


# ── Event API Helpers ─────────────────────────────────────────────────────────

def _api(method: str, path: str, **kw) -> requests.Response:
    headers = {**kw.pop("headers", {}), "Content-Type": "application/json", **internal_headers()}
    return requests.request(method, f"{catalog.url('event').rstrip('/')}{path}", headers=headers, timeout=10, **kw)


def _get_pending_deliveries() -> list[dict]:
    try:
        r = _api("GET", "/events/deliveries", params={"status": "pending", "limit": 100})
        if r.status_code in (200, 201):
            return r.json()
        log.error("Fetching pending deliveries: HTTP %d", r.status_code)
    except Exception as e:
        log.error("Failed to fetch pending deliveries: %s", e)
    return []


def _get_subscription(sub_id: int) -> dict | None:
    try:
        r = _api("GET", f"/events/internal/subscriptions/{sub_id}")
        if r.status_code in (200, 201):
            return r.json()
    except Exception as e:
        log.error("Failed to get subscription %s: %s", sub_id, e)
    return None


def _update_delivery(delivery_id: int, status: str, response_code: int | None, attempts: int,
                     last_error: str | None = None, next_retry_at: datetime | None = None) -> bool:
    body = {
        "status": status,
        "response_code": response_code,
        "attempts": attempts,
        "next_retry_at": next_retry_at.isoformat() if next_retry_at else None,
        "last_error": last_error,
    }
    try:
        r = _api("PATCH", f"/events/deliveries/{delivery_id}", json=body)
        if r.status_code < 300:
            return True
        log.error("PATCH delivery %s: HTTP %d", delivery_id, r.status_code)
    except Exception as e:
        log.error("Failed to update delivery %s: %s", delivery_id, e)
    return False


# ── Main Delivery Loop ────────────────────────────────────────────────────────

def process_deliveries():
    for d in _get_pending_deliveries():
        did = d["id"]
        attempts = d.get("attempt_count") or 0
        payload = d["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)

        sub = _get_subscription(d["subscription_id"])
        if not sub:
            log.error("Subscription %s not found for delivery %s", d["subscription_id"], did)
            _update_delivery(did, "failed", None, attempts + 1, "Subscription not found")
            continue

        code, resp = _deliver_webhook(sub["endpoint_url"], sub.get("secret", ""), d["event_type"], str(did), payload)
        attempts += 1
        if 200 <= code < 300:
            _update_delivery(did, "success", code, attempts)
            log.info("Delivery %s succeeded (HTTP %d)", did, code)
        elif attempts >= MAX_RETRY_ATTEMPTS:
            _update_delivery(did, "failed", code, attempts, resp)
            log.warning("Delivery %s failed permanently after %d attempts", did, attempts)
        else:
            delay = backoff_seconds(attempts)
            _update_delivery(did, "pending", code, attempts, resp,
                             datetime.now(timezone.utc) + timedelta(seconds=delay))
            log.info("Delivery %s failed (%s), retry in %ds", did, code, delay)


def main_loop():
    """Main worker loop."""
    log.info("Event Worker started (poll_interval=%ds, max_retries=%d)",
             POLL_INTERVAL, MAX_RETRY_ATTEMPTS)
    
    while True:
        try:
            process_deliveries()
        except Exception as e:
            log.exception("Error in delivery loop: %s", e)
        
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main_loop()
