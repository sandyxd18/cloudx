"""lb-worker: runs the haproxy of every ALB placed on this compute node.

For each load balancer it owns, the worker keeps:
  netns qlb-<id>   holding an OVS internal port on br-int whose iface-id is the VIP port, so ovn-controller binds the
                   VIP port to this node and the VIP answers from inside the namespace;
  haproxy          started inside that namespace from the config rendered by shared.haproxy_cfg.
It polls load-balancer-api (/internal/lb-agent/<host>) and reconciles; it reports haproxy's own health-check result back.
ponytail: poll every 5s; subscribe to cloudx.events if config changes must apply sooner.
"""
from __future__ import annotations

from shared.config import load_service_config

load_service_config("lb-worker")

import hashlib
import logging
import os
import socket
import time
from pathlib import Path

import requests
from shared.haproxy_cfg import render
from shared.internal import internal_headers
from shared.proc import run

log = logging.getLogger("lb-worker")
API = os.getenv("LOAD_BALANCER_API_URL", "http://127.0.0.1:8008").rstrip("/")
HOST = os.getenv("NODE_NAME") or os.getenv("COMPUTE_HOST", "")
ROOT = Path(os.getenv("LB_STATE_DIR", "/var/lib/cloudx/lb"))
INTERVAL = int(os.getenv("LB_WORKER_INTERVAL", "5"))

_applied: dict[str, str] = {}   # lb id -> sha of the config haproxy is running
_reported: dict[str, str] = {}  # member id -> last status sent


def sh(*cmd: str, ok: bool = False) -> str:
    p = run(list(cmd), check=False, timeout=30)
    if p.returncode and not ok:
        raise RuntimeError(f"{' '.join(cmd[:4])} failed: {(p.stderr or p.stdout).strip()[:200]}")
    return p.stdout


def names(lb_id: str) -> tuple[str, str]:
    return f"qlb-{lb_id[:8]}", f"lb{lb_id[:8]}"


def ensure_netns(lb: dict) -> None:
    ns, dev = names(lb["id"])
    if ns not in sh("ip", "netns", "list", ok=True):
        sh("ip", "netns", "add", ns)
    sh("ovs-vsctl", "--may-exist", "add-port", "br-int", dev, "--", "set", "Interface", dev, "type=internal",
       f"external_ids:iface-id={lb['vip_port_name']}", f"external_ids:cloudx_lb={lb['id']}")
    if run(["ip", "link", "show", dev], check=False).returncode == 0:  # still in the root namespace
        sh("ip", "link", "set", dev, "netns", ns)
    x = ["ip", "netns", "exec", ns, "ip"]
    sh(*x, "link", "set", dev, "address", lb["vip_mac"])
    sh(*x, "addr", "replace", f"{lb['vip_address']}/{lb['vip_prefix']}", "dev", dev)
    sh(*x, "link", "set", dev, "up")
    sh(*x, "link", "set", "lo", "up")
    if lb.get("vip_gateway"):
        sh(*x, "route", "replace", "default", "via", lb["vip_gateway"])


def pid_of(d: Path) -> int | None:
    try:
        pid = int((d / "haproxy.pid").read_text().split()[0])
        return pid if Path(f"/proc/{pid}").exists() else None
    except (OSError, ValueError, IndexError):
        return None


def apply_lb(lb: dict, listeners: list[dict]) -> None:
    d = ROOT / lb["id"]
    d.mkdir(parents=True, exist_ok=True)
    for ls in listeners:  # the API names the path and sends the PEM; the key never touches the controller disk
        if ls.get("ssl_pem"):
            p = Path(ls["ssl_cert_path"]); p.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(ls["ssl_pem"])
    cfg = render(lb, listeners, stats_socket=str(d / "stats.sock"))
    digest = hashlib.sha256(cfg.encode()).hexdigest()
    pid = pid_of(d)
    if _applied.get(lb["id"]) == digest and pid:
        return
    ensure_netns(lb)
    (d / "haproxy.cfg").write_text(cfg)
    os.chmod(d / "haproxy.cfg", 0o600)  # may reference a TLS key path
    sh("haproxy", "-c", "-q", "-f", str(d / "haproxy.cfg"))
    ns = names(lb["id"])[0]
    cmd = ["ip", "netns", "exec", ns, "haproxy", "-D", "-f", str(d / "haproxy.cfg"), "-p", str(d / "haproxy.pid")]
    sh(*cmd, *(["-sf", str(pid)] if pid else []))
    _applied[lb["id"]] = digest
    log.info("lb %s: haproxy %s", lb["id"], "reloaded" if pid else "started")


def remove_lb(lb_id: str) -> None:
    ns, dev = names(lb_id)
    dirs_to_remove = []
    if ROOT.exists():
        d_exact = ROOT / lb_id
        if d_exact.exists():
            dirs_to_remove.append(d_exact)
        else:
            for d in ROOT.glob("*"):
                if d.is_dir() and d.name.startswith(lb_id[:8]):
                    dirs_to_remove.append(d)

    for d in dirs_to_remove:
        pid = pid_of(d)
        if pid:
            sh("kill", str(pid), ok=True)
        for f in d.glob("*"):
            f.unlink(missing_ok=True)
        d.rmdir()

    sh("ovs-vsctl", "--if-exists", "del-port", "br-int", dev, ok=True)
    sh("ip", "netns", "del", ns, ok=True)

    for k in list(_applied.keys()):
        if k == lb_id or k.startswith(lb_id[:8]):
            _applied.pop(k, None)
    log.info("lb %s: removed", lb_id)


def member_status(lb_id: str) -> dict[str, str]:
    """member id -> ONLINE|OFFLINE from haproxy's stats socket."""
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(5)
    try:
        s.connect(str(ROOT / lb_id / "stats.sock"))
        s.sendall(b"show stat\n")
        buf = b""
        while chunk := s.recv(65536):
            buf += chunk
    finally:
        s.close()
    lines = buf.decode().splitlines()
    head = lines[0].lstrip("# ").split(",")
    out = {}
    for ln in lines[1:]:
        f = ln.split(",")
        if len(f) > head.index("status") and f[1].startswith("m_"):
            st = f[head.index("status")]
            if st.startswith("UP") or st == "no check":
                out[f[1][2:]] = "ONLINE"
            elif st.startswith("DOWN"):
                out[f[1][2:]] = "OFFLINE"
    return out


def tick() -> None:
    r = requests.get(f"{API}/internal/lb-agent/{HOST}", headers=internal_headers(), timeout=10)
    r.raise_for_status()
    want = {x["lb"]["id"]: x for x in r.json()["load_balancers"]}
    for lb_id, x in want.items():
        try:
            apply_lb(x["lb"], x["listeners"])
        except Exception as exc:  # best-effort: one broken LB must not stop the others
            log.error("lb %s: %s", lb_id, exc)

    stale_ids = set(_applied.keys())
    if ROOT.exists():
        for d in ROOT.glob("*"):
            if d.is_dir():
                stale_ids.add(d.name)

    try:
        netns_out = sh("ip", "netns", "list", ok=True)
        for line in netns_out.splitlines():
            parts = line.strip().split()
            if parts and parts[0].startswith("qlb-"):
                prefix = parts[0][4:]
                stale_ids.add(prefix)
    except Exception as exc:
        log.debug("failed to list netns: %s", exc)

    want_prefixes = {lb_id[:8] for lb_id in want}
    for lb_id in stale_ids:
        if lb_id not in want and lb_id[:8] not in want_prefixes:
            remove_lb(lb_id)

    changed = {}
    for lb_id in want:
        try:
            for mid, st in member_status(lb_id).items():
                if _reported.get(mid) != st:
                    changed[mid] = st
        except OSError:
            continue  # haproxy not up yet
    if changed:
        requests.post(f"{API}/internal/lb-agent/{HOST}/members", json={"statuses": changed},
                      headers=internal_headers(), timeout=10).raise_for_status()
        _reported.update(changed)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s  %(message)s")
    if not HOST:
        raise SystemExit("lb-worker: node_name is not set")
    ROOT.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            tick()
        except Exception as exc:  # best-effort: API restarting or briefly unreachable; retry next tick
            log.warning("tick failed: %s", exc)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
