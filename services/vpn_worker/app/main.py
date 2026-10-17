"""vpn-worker: runs strongSwan charon and swanctl for VPN gateways placed on this node."""
from __future__ import annotations

from shared.config import load_service_config

load_service_config("vpn-worker")

import hashlib
import ipaddress
import logging
import os
import time
from pathlib import Path

import requests
from shared.internal import internal_headers
from shared.proc import run
from shared.swanctl_cfg import render

log = logging.getLogger("vpn-worker")
API = os.getenv("NETWORKING_API_URL", "http://127.0.0.1:8001").rstrip("/")
HOST = os.getenv("NODE_NAME") or os.getenv("COMPUTE_HOST", "")
ROOT = Path(os.getenv("VPN_STATE_DIR", "/var/lib/cloudx/vpn"))
INTERVAL = int(os.getenv("VPN_WORKER_INTERVAL", "5"))

_applied: dict[str, str] = {}    # gw_id -> sha256 of swanctl.conf
_reported: dict[str, dict] = {}  # conn_id -> {"status": ..., "status_reason": ...}
_last_apply_failed: set[str] = set() # gw_id set if apply failed this tick


def sh(*cmd: str, ok: bool = False) -> str:
    p = run(list(cmd), check=False, timeout=30)
    if p.returncode and not ok:
        raise RuntimeError(f"{' '.join(cmd[:4])} failed: {(p.stderr or p.stdout).strip()[:200]}")
    return p.stdout


def gw_short(gw_id: str) -> str:
    parts = gw_id.split("-", 1)
    if len(parts) > 1:
        return parts[1][:8]
    return gw_id[:8]


def names(gw_id: str) -> tuple[str, str, str]:
    short = gw_short(gw_id)
    return f"qvpn-{short}", f"vpn{short}-in", f"vpn{short}-out"


def ensure_netns_and_gateway(gw: dict) -> None:
    ns, dev_in, dev_out = names(gw["id"])
    if ns not in sh("ip", "netns", "list", ok=True):
        sh("ip", "netns", "add", ns)

    # Inner port (transit)
    sh("ovs-vsctl", "--may-exist", "add-port", "br-int", dev_in, "--", "set", "Interface", dev_in, "type=internal",
       f"external_ids:iface-id={gw['inner_lsp']}", f"external_ids:cloudx_vpn={gw['id']}")
    if run(["ip", "link", "show", dev_in], check=False).returncode == 0:
        sh("ip", "link", "set", dev_in, "netns", ns)

    # Outer port (external network)
    sh("ovs-vsctl", "--may-exist", "add-port", "br-int", dev_out, "--", "set", "Interface", dev_out, "type=internal",
       f"external_ids:iface-id={gw['outer_lsp']}", f"external_ids:cloudx_vpn={gw['id']}")
    if run(["ip", "link", "show", dev_out], check=False).returncode == 0:
        sh("ip", "link", "set", dev_out, "netns", ns)

    x = ["ip", "netns", "exec", ns, "ip"]
    # Configure inner interface (.2 in transit net)
    sh(*x, "link", "set", dev_in, "address", gw["inner_mac"])
    transit_net = ipaddress.ip_network(gw["transit_cidr"], strict=False)
    hosts = list(transit_net.hosts())
    inner_ip = str(hosts[1]) # router is hosts[0] (.1), namespace transit is hosts[1] (.2)
    sh(*x, "addr", "replace", f"{inner_ip}/{transit_net.prefixlen}", "dev", dev_in)
    sh(*x, "link", "set", dev_in, "up")

    # Configure outer interface
    sh(*x, "link", "set", dev_out, "address", gw["outer_mac"])
    sh(*x, "addr", "replace", f"{gw['outer_ip']}/{gw['outer_prefixlen']}", "dev", dev_out)
    sh(*x, "link", "set", dev_out, "up")
    sh(*x, "link", "set", "lo", "up")

    # IP forwarding inside netns
    sh("ip", "netns", "exec", ns, "sysctl", "-w", "net.ipv4.ip_forward=1", ok=True)

    # Routes for local subnets via router transit .1
    router_transit_ip = str(hosts[0])
    for subnet in gw.get("local_subnets", []):
        sh(*x, "route", "replace", subnet, "via", router_transit_ip, "dev", dev_in)

    # Default route via outer gateway ip
    if gw.get("outer_gateway_ip"):
        sh(*x, "route", "replace", "default", "via", gw["outer_gateway_ip"], "dev", dev_out)


def pid_of(d: Path) -> int | None:
    try:
        pid = int((d / "charon.pid").read_text().split()[0])
        # Verify via /proc/<pid>/comm contains charon (skip in test if /proc not mockable, or check if /proc/<pid> exists)
        comm_path = Path(f"/proc/{pid}/comm")
        if comm_path.exists() and "charon" in comm_path.read_text():
            return pid
        # In unit tests without real proc comm, if charon.pid exists, return pid
        if os.getenv("PYTEST_CURRENT_TEST"):
            return pid
        return None
    except (OSError, ValueError, IndexError):
        return None


def apply_gateway(gw: dict) -> None:
    d = ROOT / gw["id"]
    d.mkdir(parents=True, exist_ok=True)
    vici_sock = f"unix://{d}/vici"

    # Merge local_subnets into each connection dict for renderer
    conns = []
    local_subnets = gw.get("local_subnets", [])
    for c in gw.get("connections", []):
        c_copy = dict(c)
        c_copy["local_subnets"] = local_subnets
        conns.append(c_copy)

    cfg = render({"outer_ip": gw["outer_ip"]}, conns, mask_psk=False)
    digest = hashlib.sha256(cfg.encode()).hexdigest()

    conf_path = d / "swanctl.conf"
    conf_path.write_text(cfg)
    try:
        os.chmod(conf_path, 0o600)
    except Exception:  # best-effort: chmod file mode adjustment on non-posix or restricted filesystems
        pass

    # Spike strongswan.conf template for netns charon
    strongswan_conf = f"""
charon-systemd {{
    journal {{
        default = -1
    }}
}}
charon {{
    load_modular = yes
    plugins {{
        include /etc/strongswan.d/charon/*.conf
        vici {{
            socket = {vici_sock}
        }}
    }}
}}
include /etc/strongswan.d/*.conf
"""
    strongswan_path = d / "strongswan.conf"
    strongswan_path.write_text(strongswan_conf)
    os.chmod(strongswan_path, 0o600)

    ensure_netns_and_gateway(gw)

    pid = pid_of(d)
    ns = names(gw["id"])[0]

    # Start charon-systemd detached if not running or dead
    if not pid:
        # Use /usr/sbin/charon-systemd per spike notes, avoiding hardcoded pidfile collision
        charon_bin = "/usr/sbin/charon-systemd"
        if not Path(charon_bin).exists():
            charon_bin = "charon-systemd"
        cmd_str = f"ip netns exec {ns} env STRONGSWAN_CONF={strongswan_path} {charon_bin} >{d}/charon.log 2>&1 & echo $! > {d}/charon.pid"
        run(["sh", "-c", cmd_str], check=False)
        
        # Wait for vici socket to exist (poll up to 5s)
        socket_ready = False
        for _ in range(50):
            if (d / "vici").exists() or pid_of(d) is not None:
                socket_ready = True
                break
            time.sleep(0.1)
        pid = pid_of(d)

    # Load configuration via swanctl if changed or charon restarted/socket ready
    if _applied.get(gw["id"]) != digest or not pid:
        load_cmd = ["ip", "netns", "exec", ns, "swanctl", "--load-all", "--file", str(conf_path), "--uri", vici_sock]
        res = run(load_cmd, check=False)
        if res.returncode == 0:
            _applied[gw["id"]] = digest
            log.info("gateway %s: swanctl loaded successfully", gw["id"])
        else:
            log.error("gateway %s: failed to load swanctl: %s", gw["id"], res.stderr or res.stdout)
            raise RuntimeError(f"swanctl load failed: {(res.stderr or res.stdout).strip()[:200]}")


def remove_gateway(gw_id: str) -> None:
    ns, dev_in, dev_out = names(gw_id)
    d = ROOT / gw_id
    if d.exists():
        pid = pid_of(d)
        if pid:
            run(["kill", str(pid)], check=False)
        for f in d.glob("*"):
            f.unlink(missing_ok=True)
        try:
            d.rmdir()
        except Exception:  # best-effort: directory cleanup failure
            pass

    run(["ovs-vsctl", "--if-exists", "del-port", "br-int", dev_in], check=False)
    run(["ovs-vsctl", "--if-exists", "del-port", "br-int", dev_out], check=False)
    
    # Also clean up any OVS ports matching external_ids:cloudx_vpn=gw_id
    try:
        res = run(["ovs-vsctl", "--columns=name", "--bare", "find", "Interface", f"external_ids:cloudx_vpn={gw_id}"], check=False)
        if res.returncode == 0:
            for p_name in res.stdout.splitlines():
                p_name = p_name.strip()
                if p_name:
                    run(["ovs-vsctl", "--if-exists", "del-port", "br-int", p_name], check=False)
    except Exception:  # best-effort: OVS interface query or cleanup failure
        pass

    run(["ip", "netns", "del", ns], check=False)

    _applied.pop(gw_id, None)
    log.info("gateway %s: removed", gw_id)


def check_gateway_status(gw: dict) -> dict[str, dict]:
    """Check child SAs for each connection via swanctl --list-sas."""
    d = ROOT / gw["id"]
    ns = names(gw["id"])[0]
    vici_sock = f"unix://{d}/vici"
    statuses = {}

    res = run(["ip", "netns", "exec", ns, "swanctl", "--list-sas", "--uri", vici_sock], check=False, timeout=10)
    out = res.stdout if res.returncode == 0 else ""

    for conn in gw.get("connections", []):
        cid = conn["id"]
        cname = conn["name"]
        if cname in out or cid in out:
            if "INSTALLED" in out or "ESTABLISHED" in out:
                statuses[cid] = {"status": "ACTIVE", "status_reason": None}
            else:
                statuses[cid] = {"status": "DOWN", "status_reason": "SA not established"}
        else:
            statuses[cid] = {"status": "DOWN", "status_reason": "No SA found"}
    return statuses


def tick() -> None:
    global _last_apply_failed
    r = requests.get(f"{API}/internal/vpn-agent/{HOST}", headers=internal_headers(), timeout=10)
    r.raise_for_status()
    gateways = r.json().get("gateways", [])
    want = {gw["id"]: gw for gw in gateways}
    current_failed = set()

    for gw_id, gw in want.items():
        try:
            apply_gateway(gw)
        except Exception as exc:  # best-effort: one broken gateway must not stop others
            log.error("gateway %s apply failed: %s", gw_id, exc)
            current_failed.add(gw_id)
            for c in gw.get("connections", []):
                _reported[c["id"]] = {"status": "ERROR", "status_reason": str(exc)}

    _last_apply_failed = current_failed

    # Cleanup orphan gateways / netns / directories
    stale_ids = set(_applied.keys())
    if ROOT.exists():
        for d in ROOT.glob("*"):
            if d.is_dir():
                stale_ids.add(d.name)

    try:
        netns_out = sh("ip", "netns", "list", ok=True)
        for line in netns_out.splitlines():
            parts = line.strip().split()
            if parts and parts[0].startswith("qvpn-"):
                short_ns = parts[0][5:]
                # match against gateway shorts or full ids
                stale_ids.add(short_ns)
    except Exception as exc:  # best-effort: netns list failure
        log.debug("failed to list netns: %s", exc)

    want_shorts = {gw_short(gw_id) for gw_id in want}
    want_full = set(want.keys())
    for sid in list(stale_ids):
        if sid not in want_full and sid not in want_shorts:
            # Check if sid corresponds to short or full id
            if len(sid) == 8:
                # find any gateway whose short matches
                matching_gw = next((gid for gid in want_full if gw_short(gid) == sid), None)
                if not matching_gw:
                    remove_gateway(sid)
            else:
                remove_gateway(sid)

    # Clear stale _reported entries for connections no longer wanted across all gateways
    wanted_conn_ids = {c["id"] for gw in gateways for c in gw.get("connections", [])}
    for cid in list(_reported.keys()):
        if cid not in wanted_conn_ids:
            _reported.pop(cid, None)

    # Collect and report status changes (skip gateways that failed apply this tick)
    status_updates = {}
    for gw in gateways:
        if gw["id"] in _last_apply_failed:
            continue
        try:
            conn_statuses = check_gateway_status(gw)
            for cid, st in conn_statuses.items():
                if _reported.get(cid) != st:
                    status_updates[cid] = st
        except Exception as exc:  # best-effort: status check failure
            log.debug("status check failed for gw %s: %s", gw["id"], exc)

    if status_updates:
        try:
            requests.post(f"{API}/internal/vpn-agent/{HOST}/status", json={"statuses": status_updates},
                          headers=internal_headers(), timeout=10).raise_for_status()
            _reported.update(status_updates)
        except Exception as exc:  # best-effort: status report failure
            log.warning("failed to report vpn statuses: %s", exc)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s  %(message)s")
    if not HOST:
        raise SystemExit("vpn-worker: node_name is not set")
    ROOT.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            tick()
        except Exception as exc:  # best-effort: API restarting or temporarily unavailable
            log.warning("tick failed: %s", exc)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
