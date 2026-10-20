"""ha-watchdog: self-fencing for a compute node that ha-api can no longer hear.

If ha-api is unreachable for HA_WATCHDOG_TIMEOUT seconds and the node is isolated (a peer still reaches ha-api, or no
peer answers), the protected VMs of this node are destroyed so ha-api can evacuate them without a duplicate running.
Deliberately not a thread of compute-worker: a hung worker must not take the watchdog down with it.
ponytail: needs at least two compute nodes (peers); the protected list can be a few seconds stale.
"""
from __future__ import annotations

from shared.config import load_service_config

load_service_config("ha-watchdog")

import hmac
import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import requests
from shared.internal import internal_headers
from shared.proc import run
from shared.watchdog_logic import decide

log = logging.getLogger("ha-watchdog")
HA_APIS = [u.strip().rstrip("/") for u in os.getenv("HA_API_URL", "http://127.0.0.1:8020").split(",") if u.strip()]  # several ha-api instances
HOST = os.getenv("HA_WATCHDOG_HOST") or os.getenv("COMPUTE_HOST") or os.getenv("NODE_NAME", "")
PEERS = [p.strip().rstrip("/") for p in os.getenv("HA_WATCHDOG_PEERS", "").split(",") if p.strip()]
TIMEOUT = float(os.getenv("HA_WATCHDOG_TIMEOUT", "45"))
DRY_RUN = os.getenv("HA_WATCHDOG_DRY_RUN", "false").lower() in ("1", "true", "yes")
PORT = int(os.getenv("HA_WATCHDOG_PORT", "8021"))
BIND = os.getenv("HA_WATCHDOG_BIND", "0.0.0.0")
LIBVIRT_URI = os.getenv("LIBVIRT_URI", "qemu:///system")
STATE_DIR = Path(os.getenv("HA_WATCHDOG_STATE_DIR", "/var/lib/cloudx/ha-watchdog"))
FLAG = STATE_DIR / "fenced"   # compute-worker reads this path: while it exists, no auto-restart
CACHE = STATE_DIR / "protected.json"
INTERVAL = 5
FRESH = 20  # a peer counts our contact with ha-api as alive for this long


class State:
    def __init__(self, now: float):
        self.last_ok = now
        self.killed = False


def fetch(method: str, path: str):
    """(reachable, response). Any HTTP answer, even a 5xx or a refused connection, means the network path works."""
    reachable, fallback = False, None
    for base in HA_APIS:  # first instance that answers properly wins; a 5xx only counts as reachable
        try:
            r = requests.request(method, base + path, headers=internal_headers(), timeout=5)
        except requests.ConnectionError as exc:
            reachable = reachable or "refused" in str(exc).lower() or "111" in str(exc)
            continue
        except requests.RequestException:
            continue
        if r.status_code < 500:
            return True, r
        reachable, fallback = True, fallback or r
    return reachable, fallback


def ask_peers() -> list[bool]:
    out = []
    for url in PEERS:
        try:
            r = requests.get(url + "/reach", headers=internal_headers(), timeout=3)
            if r.status_code == 200:
                out.append(bool(r.json().get("ha_api")))
        except (requests.RequestException, ValueError):
            continue
    return out


def load_cache() -> list[str]:
    try:
        return json.loads(CACHE.read_text()).get("domains", [])
    except (OSError, ValueError):
        return []


def kill(domains: list[str]) -> None:
    if DRY_RUN:
        log.warning("dry-run: would destroy %s", domains)
        return
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    FLAG.write_text(json.dumps(domains))  # before destroy, so compute-worker cannot restart what we kill
    for d in domains:
        r = run(["virsh", "-c", LIBVIRT_URI, "destroy", d], check=False)
        log.warning("fenced %s (rc=%s)", d, r.returncode)


def step(st: State, now: float) -> None:
    ok, r = fetch("GET", f"/internal/protected-domains/{HOST}")
    if ok:
        st.last_ok, st.killed = now, False
        if r is not None and r.status_code == 200:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            CACHE.write_text(json.dumps(r.json()))
            fetch("POST", f"/internal/watchdog/{HOST}")
            if FLAG.exists():
                _, h = fetch("GET", f"/internal/hosts/{HOST}")
                if h is not None and h.status_code == 200 and h.json().get("state") == "up":
                    FLAG.unlink(missing_ok=True)
        return
    if st.killed or now - st.last_ok < TIMEOUT:
        return
    domains = load_cache()
    verdict = decide(now, st.last_ok, TIMEOUT, ask_peers(), bool(PEERS), domains)
    log.warning("ha-api silent for %.0fs: %s", now - st.last_ok, verdict)
    if verdict == "kill":
        kill(domains)
        st.killed = True


def serve_reach(st: State) -> None:
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            token = os.getenv("INTERNAL_TOKEN", "")
            if self.path != "/reach" or not token or not hmac.compare_digest(self.headers.get("X-Internal-Token", ""), token):
                self.send_response(401)
                self.end_headers()
                return
            body = json.dumps({"ha_api": time.monotonic() - st.last_ok < FRESH}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    ThreadingHTTPServer((BIND, PORT), H).serve_forever()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    if not HOST:
        raise SystemExit("ha-watchdog: set ha_watchdog_host (the node name ha-api knows)")
    st = State(time.monotonic())
    threading.Thread(target=serve_reach, args=(st,), daemon=True).start()
    while True:
        try:
            step(st, time.monotonic())
        except Exception:  # best-effort: one bad pass must not stop the watchdog
            log.exception("watchdog step failed")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
