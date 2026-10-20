"""reaper: deletes Database, Container and File Store rows that failed during build and were never ready.

Runs as root on the controller because it logs in as admin with auth_admin_password from cloudx.conf (the API
processes cannot read that file). Every delete goes through the service's own public DELETE, so cleanup of the
downstream resources is exactly what a user-triggered delete does.
ponytail: one pass every REAPER_INTERVAL seconds, no queue; a row whose delete keeps failing is retried each pass.
"""
from __future__ import annotations

from shared.config import load_service_config

load_service_config("reaper")

import logging
import os
import time

import requests
from shared.internal import internal_headers

log = logging.getLogger("reaper")
SERVICES = {
    "file-store": os.getenv("FILE_STORE_API_URL", "http://127.0.0.1:8017").rstrip("/"),
    "database": os.getenv("DATABASE_API_URL", "http://127.0.0.1:8019").rstrip("/"),
    "container": os.getenv("CONTAINER_API_URL", "http://127.0.0.1:8018").rstrip("/"),
}
# kind reported by /internal/stale-errors -> public DELETE path. File Store lists shares before share networks,
# and a share network that still has a share is refused (409) and simply retried on the next pass.
DELETE = {"share": "/shares/", "share-network": "/share-networks/", "database": "/instances/", "container": "/containers/"}
AUTH_URL = os.getenv("AUTH_API_URL", "http://127.0.0.1:8005").rstrip("/")
MAX_AGE_HOURS = float(os.getenv("REAPER_MAX_AGE_HOURS", "24"))
INTERVAL = int(os.getenv("REAPER_INTERVAL", "600"))
DRY_RUN = os.getenv("REAPER_DRY_RUN", "false").lower() in ("1", "true", "yes")


def login() -> str | None:
    try:
        r = requests.request("POST", f"{AUTH_URL}/auth/tokens", timeout=10,
                             json={"username": "admin", "password": os.getenv("AUTH_ADMIN_PASSWORD", "")})
        return r.json()["token"] if r.status_code in (200, 201) else None
    except Exception as exc:  # best-effort: auth-api down means skip this pass, never crash the loop
        log.warning("admin login failed: %s", exc)
        return None


def cycle() -> None:
    token = login()
    if not token:
        log.warning("no admin token; skipping this pass")
        return
    for name, base in SERVICES.items():
        try:
            r = requests.request("GET", f"{base}/internal/stale-errors", params={"hours": MAX_AGE_HOURS},
                                 headers=internal_headers(), timeout=10)
            r.raise_for_status()
            items = r.json()["items"]
        except Exception as exc:  # best-effort: one service being down must not stop the others
            log.warning("%s: could not list stale errors: %s", name, exc)
            continue
        for it in items:
            url = f"{base}{DELETE[it['kind']]}{it['id']}"
            if DRY_RUN:
                log.info("dry-run: would delete %s %s", it["kind"], it["id"])
                continue
            try:
                requests.request("DELETE", url, headers={"X-Auth-Token": token}, timeout=30).raise_for_status()
                log.info("deleted %s %s (failed during build, older than %sh)", it["kind"], it["id"], MAX_AGE_HOURS)
            except Exception as exc:  # best-effort: record and carry on; retried next pass
                log.warning("could not delete %s %s: %s", it["kind"], it["id"], exc)
    backups(token)


def backups(token: str) -> None:
    """Scheduled database backups: create the due ones, then delete scheduled backups beyond `keep`."""
    base = SERVICES["database"]
    for path, what in (("backup-due", "create"), ("backup-expired", "delete")):
        try:
            r = requests.request("GET", f"{base}/internal/{path}", headers=internal_headers(), timeout=10)
            r.raise_for_status()
            items = r.json()["items"]
        except Exception as exc:  # best-effort: skip this half, try again next pass
            log.warning("database: could not list %s: %s", path, exc)
            continue
        for it in items:
            if DRY_RUN:
                log.info("dry-run: would %s backup for %s", what, it["id"])
                continue
            try:
                if what == "create":
                    r = requests.request("POST", f"{base}/instances/{it['id']}/backups", params={"scheduled": "1"}, json={},
                                         headers={"X-Auth-Token": token}, timeout=30)
                else:
                    r = requests.request("DELETE", f"{base}/backups/{it['id']}", headers={"X-Auth-Token": token}, timeout=30)
                r.raise_for_status()
                log.info("scheduled backup: %s %s", what, it["id"])
            except Exception as exc:  # best-effort: record and carry on; retried next pass
                log.warning("scheduled backup: could not %s %s: %s", what, it["id"], exc)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
    log.info("reaper started: max age %sh, every %ss, dry-run=%s", MAX_AGE_HOURS, INTERVAL, DRY_RUN)
    while True:
        cycle()
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
