"""dns-worker: renders dns-api's state into two BIND instances and reloads them.

  auth      public zones, no recursion, answers on dns_public_address
  resolver  every zone + forwarders for tenants, answers on dns_resolver_address
Both run from /var/lib/cloudx/dns/<role>/ (units cloudx-named-auth / cloudx-named-resolver).
ponytail: polls every 5s and rewrites only what changed; use `rndc addzone` if reloading all zones gets slow.
"""
from __future__ import annotations

from shared.config import load_service_config

load_service_config("dns-worker")

import logging
import os
import shutil
import time
from pathlib import Path

import requests
from shared import catalog, dns_names
from shared.dns_render import hostmaster_from_email, render_named_conf, render_zone
from shared.internal import internal_headers
from shared.proc import run

log = logging.getLogger("dns-worker")
ROOT = Path(os.getenv("DNS_STATE_DIR", "/var/lib/cloudx/dns"))
INTERVAL = int(os.getenv("DNS_WORKER_INTERVAL", "5"))
SWEEP_INTERVAL = int(os.getenv("DNS_SWEEP_INTERVAL", "60"))
PORTS = {"auth": 953, "resolver": 954}


def csv(name: str) -> list[str]:
    return [x.strip() for x in os.getenv(name, "").split(",") if x.strip()]


def role_cfg(role: str) -> dict:
    addr = os.getenv("DNS_PUBLIC_ADDRESS", "172.20.0.120") if role == "auth" else os.getenv("DNS_RESOLVER_ADDRESS", "172.20.0.118")
    return {"root": str(ROOT / role), "address": addr, "control_port": PORTS[role],
            "forwarders": csv("DNS_UPSTREAM"), "clients": csv("DNS_RESOLVER_CLIENTS")}


def write(path: Path, text: str) -> bool:
    if path.exists() and path.read_text() == text:
        return False
    path.write_text(text)
    return True


def sync(role: str, state: dict) -> tuple[bool, set[str]]:
    """Write conf and zone files for one role. Returns (changed, names of zones that are now served)."""
    cfg = role_cfg(role)
    root = Path(cfg["root"])
    (root / "zones").mkdir(parents=True, exist_ok=True)
    if not (root / "rndc.key").exists():
        run(["rndc-confgen", "-a", "-A", "hmac-sha256", "-k", "rndc-key", "-c", str(root / "rndc.key")], check=True, timeout=30)
    ns = f"ns1.{state['base']}."
    zones = [z for z in state["zones"] if not (role == "auth" and z["scope"] == "internal")]
    changed, served = False, set()
    for z in zones:
        f = root / "zones" / f"{z['name']}.zone"
        body = render_zone(z, z["recordsets"], ns, hostmaster_from_email(z["email"]))
        tmp = f.with_suffix(".tmp")
        tmp.write_text(body)
        if run(["named-checkzone", "-q", z["name"], str(tmp)], check=False, timeout=30).returncode:
            log.error("zone %s does not pass named-checkzone; keeping the previous file", z["name"])
            tmp.unlink()
            if f.exists():
                served.add(z["name"])
            continue
        if f.exists() and f.read_text() == body:
            tmp.unlink()
        else:
            tmp.replace(f)
            changed = True
        served.add(z["name"])
    keep = {f"{n}.zone" for n in served}
    for old in (root / "zones").glob("*.zone"):
        if old.name not in keep:
            old.unlink()
            changed = True
    conf = render_named_conf(role, [z for z in zones if z["name"] in served], cfg)
    changed |= write(root / "named.conf", conf)
    if changed or run(["rndc", "-s", "127.0.0.1", "-p", str(cfg["control_port"]), "-k", str(root / "rndc.key"), "status"], check=False, timeout=10).returncode:
        shutil.chown(root, "bind", "bind")
        for p in root.rglob("*"):
            shutil.chown(p, "bind", "bind")
    return changed, served


def reload(role: str) -> bool:
    root = ROOT / role
    r = run(["rndc", "-s", "127.0.0.1", "-p", str(PORTS[role]), "-k", str(root / "rndc.key"), "reload"], check=False, timeout=30)
    if r.returncode:  # named is not up yet (first start or crashed): let systemd bring it up with the new files
        r = run(["systemctl", "restart", f"cloudx-named-{role}"], check=False, timeout=60)
    return r.returncode == 0


SWEPT = ("floating_ip", "load_balancer", "load_balancer_internal", "server")  # buckets register on create/delete only


def sweep(want: list[dict], have: dict) -> tuple[list[dict], list[str]]:
    """(records to register, owners to remove). `have` is dns-api's /internal/records "records" map."""
    todo = [w for w in want if (h := have.get(w["owner"])) is None or (h["fqdn"], h["records"]) != (w["fqdn"], w["records"])]
    wanted = {w["owner"] for w in want}
    return todo, [o for o in have if o.split(":")[0] in SWEPT and o not in wanted]


def _get(url: str, path: str) -> list:
    r = requests.get(f"{url}{path}", headers=internal_headers(), timeout=20)
    r.raise_for_status()
    body = r.json()
    return next(iter(body.values())) if isinstance(body, dict) else body


def collect_want() -> list[dict]:
    base = dns_names.base()
    auth = os.getenv("AUTH_API_URL", "http://127.0.0.1:8005").rstrip("/")
    networking, load_balancer, compute = (catalog.url(s).rstrip("/") for s in ("network", "load-balancer", "compute"))
    r = requests.get(f"{auth}/internal/projects", headers=internal_headers(), timeout=20)
    r.raise_for_status()
    names = r.json()["projects"]
    want = []
    for f in _get(networking, "/floating-ips"):
        ip = f.get("floating_ip_address")
        if ip:
            want.append({"owner": f"floating_ip:{f['id']}", "project_id": f.get("project_id"), "scope": "public", "type": "A",
                         "fqdn": dns_names.fip_fqdn(ip, base), "records": [ip]})
    for lb in _get(load_balancer, "/load-balancers"):
        pname = names.get(lb["project_id"], lb["project_id"])
        want.append({"owner": f"load_balancer_internal:{lb['id']}", "project_id": lb["project_id"], "scope": "internal", "type": "A",
                     "fqdn": dns_names.internal_fqdn(lb["name"], pname, base), "records": [lb["vip_address"]]})
        if lb.get("vip_floating_ip"):
            want.append({"owner": f"load_balancer:{lb['id']}", "project_id": lb["project_id"], "scope": "public", "type": "A",
                         "fqdn": dns_names.lb_fqdn(lb["name"], lb["id"], base), "records": [lb["vip_floating_ip"]]})
    for s in _get(compute, "/servers?limit=1000"):
        if s.get("ip_address") and s.get("status") != "DELETED":
            pname = names.get(s["project_id"], s["project_id"])
            want.append({"owner": f"server:{s['id']}", "project_id": s["project_id"], "scope": "internal", "type": "A",
                         "fqdn": dns_names.internal_fqdn(s["name"], pname, base), "records": [s["ip_address"]]})
    return want


def run_sweep() -> None:
    api = catalog.url("dns").rstrip("/")
    have = requests.get(f"{api}/internal/records", headers=internal_headers(), timeout=10).json()["records"]
    todo, gone = sweep(collect_want(), have)
    for w in todo:
        r = requests.post(f"{api}/internal/records", json=w, headers=internal_headers(), timeout=10)
        if r.status_code >= 400:
            log.warning("sweep register %s: %s %s", w["owner"], r.status_code, r.text[:120])
    for o in gone:
        requests.delete(f"{api}/internal/records", params={"owner": o}, headers=internal_headers(), timeout=10)


def tick() -> None:
    api = catalog.url("dns").rstrip("/")
    r = requests.get(f"{api}/internal/state", headers=internal_headers(), timeout=10)
    r.raise_for_status()
    state = r.json()
    ok = True
    for role in ("auth", "resolver"):
        changed, _ = sync(role, state)
        if changed:
            ok &= reload(role)
    if ok:
        for z in state["zones"]:
            requests.post(f"{api}/internal/zones/{z['id']}/status", json={"serial": z["serial"]}, headers=internal_headers(), timeout=10)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s  %(message)s")
    last = 0.0
    while True:
        if time.monotonic() - last > SWEEP_INTERVAL:
            last = time.monotonic()
            try:
                run_sweep()
            except Exception as exc:  # best-effort: a failing sweep must not stop zone rendering
                log.warning("dns sweep failed: %s", exc)
        try:
            tick()
        except Exception as exc:  # best-effort: API or BIND down; retry on the next tick
            log.warning("dns tick failed: %s", exc)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()