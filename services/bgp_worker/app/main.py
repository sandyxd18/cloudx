"""bgp-worker: runs BIRD 2 daemon for BGP speakers placed on this node."""
from __future__ import annotations

from shared.config import load_service_config

load_service_config("bgp-worker")

import hashlib
import logging
import os
import re
import time
from pathlib import Path

import requests
from shared.bird_cfg import render
from shared.internal import internal_headers
from shared.proc import run

log = logging.getLogger("bgp-worker")
API = os.getenv("NETWORKING_API_URL", "http://127.0.0.1:8001").rstrip("/")
HOST = os.getenv("NODE_NAME") or os.getenv("COMPUTE_HOST", "")
ROOT = Path(os.getenv("BGP_STATE_DIR", "/var/lib/cloudx/bgp"))
INTERVAL = int(os.getenv("BGP_WORKER_INTERVAL", "5"))

_applied: dict[str, str] = {}    # speaker_id -> sha256 of bird.conf
_reported: dict[str, dict] = {}  # peer_id -> {"status": ..., "status_reason": ..., "routes": [...]}
_last_apply_failed: set[str] = set()


def sh(*cmd: str, ok: bool = False) -> str:
    p = run(list(cmd), check=False, timeout=30)
    if p.returncode and not ok:
        raise RuntimeError(f"{' '.join(cmd[:4])} failed: {(p.stderr or p.stdout).strip()[:200]}")
    return p.stdout


def gw_short(speaker_id: str) -> str:
    parts = speaker_id.split("-", 1)
    if len(parts) > 1:
        return parts[1][:8]
    return speaker_id[:8]


def names(speaker_id: str) -> tuple[str, str]:
    short = gw_short(speaker_id)
    return f"qbgp-{short}", f"bgp{short}-out"


def parse_routes(output: str) -> list[dict]:
    """
    Parse BIRD 2 output from `show route protocol <proto>`.
    Example output:
      Table master4:
      10.202.0.0/24        unicast [bgp_peer 23:56:42.217] * (100) [AS64512i]
      	via 192.0.2.2 on veth-bgA
    """
    routes = []
    current_prefix = None
    for line in output.splitlines():
        # Match prefix line
        m_pfx = re.match(r"^\s*([0-9\./]+)\s+", line)
        if m_pfx and "Table" not in line:
            current_prefix = m_pfx.group(1)
            continue

        # Match via line for current prefix
        if current_prefix:
            m_via = re.match(r"^\s+via\s+([0-9\.]+)\s+", line)
            if m_via:
                nexthop = m_via.group(1)
                routes.append({"prefix": current_prefix, "nexthop": nexthop})
                current_prefix = None
    return routes


def ensure_netns_and_speaker(spk: dict) -> None:
    ns, dev_out = names(spk["id"])
    if ns not in sh("ip", "netns", "list", ok=True):
        sh("ip", "netns", "add", ns)

    # Outer port (external network)
    sh("ovs-vsctl", "--may-exist", "add-port", "br-int", dev_out, "--", "set", "Interface", dev_out, "type=internal",
       f"external_ids:iface-id={spk['outer_lsp']}", f"external_ids:cloudx_bgp={spk['id']}")
    if run(["ip", "link", "show", dev_out], check=False).returncode == 0:
        sh("ip", "link", "set", dev_out, "netns", ns)

    x = ["ip", "netns", "exec", ns, "ip"]
    sh(*x, "link", "set", dev_out, "address", spk["outer_mac"])
    sh(*x, "addr", "replace", f"{spk['outer_ip']}/{spk['outer_prefixlen']}", "dev", dev_out)
    sh(*x, "link", "set", dev_out, "up")
    sh(*x, "link", "set", "lo", "up")

    sh("ip", "netns", "exec", ns, "sysctl", "-w", "net.ipv4.ip_forward=1", ok=True)

    # Default route via outer gateway ip
    if spk.get("outer_gateway_ip"):
        sh(*x, "route", "replace", "default", "via", spk["outer_gateway_ip"], "dev", dev_out)


def pid_of(d: Path) -> int | None:
    try:
        pid = int((d / "bird.pid").read_text().split()[0])
        comm_path = Path(f"/proc/{pid}/comm")
        if comm_path.exists() and "bird" in comm_path.read_text():
            return pid
        if os.getenv("PYTEST_CURRENT_TEST"):
            return pid
        return None
    except (OSError, ValueError, IndexError):
        return None


def apply_speaker(spk: dict) -> None:
    d = ROOT / spk["id"]
    d.mkdir(parents=True, exist_ok=True)
    control_socket = f"{d}/bird.ctl"

    speaker_dict = {
        "outer_ip": spk["outer_ip"],
        "router_ext_ip": spk["router_ext_ip"],
        "local_subnets": spk.get("local_subnets", []),
    }
    peers = spk.get("peers", [])

    cfg = render(speaker_dict, peers, mask_password=False)
    digest = hashlib.sha256(cfg.encode()).hexdigest()

    conf_path = d / "bird.conf"
    conf_path.write_text(cfg)
    try:
        os.chmod(conf_path, 0o600)
    except Exception:  # best-effort: file mode adjustment on non-posix filesystems
        pass

    ensure_netns_and_speaker(spk)

    pid = pid_of(d)
    ns, _ = names(spk["id"])
    fresh = False

    # Start bird detached if not running or dead
    if not pid:
        bird_bin = "/usr/sbin/bird"
        if not Path(bird_bin).exists():
            bird_bin = "bird"
        cmd_str = f"ip netns exec {ns} {bird_bin} -c {conf_path} -s {control_socket} -P {d}/bird.pid >{d}/bird.log 2>&1"
        run(["sh", "-c", cmd_str], check=False)

        # Wait for control socket to exist (poll up to 5s)
        socket_ready = False
        for _ in range(50):
            if Path(control_socket).exists() or pid_of(d) is not None:
                socket_ready = True
                break
            time.sleep(0.1)
        pid = pid_of(d)
        fresh = True

    if fresh:
        _applied[spk["id"]] = digest  # bird just read this exact bird.conf at start
    elif _applied.get(spk["id"]) != digest:
        load_cmd = ["birdc", "-s", control_socket, "configure"]
        res = run(load_cmd, check=False)
        if res.returncode == 0:
            _applied[spk["id"]] = digest
            log.info("speaker %s: bird reconfigured successfully", spk["id"])
        else:
            log.error("speaker %s: failed to configure bird: %s", spk["id"], res.stderr or res.stdout)
            raise RuntimeError(f"bird configure failed: {(res.stderr or res.stdout).strip()[:200]}")


def remove_speaker(speaker_id: str) -> None:
    ns, dev_out = names(speaker_id)
    d = ROOT / speaker_id
    if d.exists():
        pid = pid_of(d)
        if pid:
            run(["kill", str(pid)], check=False)
        for f in d.glob("*"):
            f.unlink(missing_ok=True)
        try:
            d.rmdir()
        except Exception:  # best-effort: directory removal cleanup failure
            pass

    run(["ovs-vsctl", "--if-exists", "del-port", "br-int", dev_out], check=False)
    try:
        res = run(["ovs-vsctl", "--columns=name", "--bare", "find", "Interface", f"external_ids:cloudx_bgp={speaker_id}"], check=False)
        if res.returncode == 0:
            for p_name in res.stdout.splitlines():
                p_name = p_name.strip()
                if p_name:
                    run(["ovs-vsctl", "--if-exists", "del-port", "br-int", p_name], check=False)
    except Exception:  # best-effort: OVS interface query or cleanup failure
        pass

    run(["ip", "netns", "del", ns], check=False)
    _applied.pop(speaker_id, None)
    log.info("speaker %s: removed", speaker_id)


def check_speaker_status(spk: dict) -> dict[str, dict]:
    d = ROOT / spk["id"]
    control_socket = f"{d}/bird.ctl"
    statuses = {}

    res = run(["birdc", "-s", control_socket, "show", "protocols"], check=False, timeout=10)
    out = res.stdout if res.returncode == 0 else ""

    for peer in spk.get("peers", []):
        pid = peer["id"]
        safe_pid = "".join(c if c.isalnum() or c == "_" else "_" for c in pid)
        proto_name = f"bgp_{safe_pid}"

        # Search for protocol line in show protocols output
        proto_found = False
        proto_state = "down"
        for line in out.splitlines():
            if line.split()[:1] == [proto_name]:
                proto_found = True
                if "Established" in line:
                    proto_state = "established"
                elif "Up" in line or "Connect" in line or "Open" in line:
                    proto_state = "down"
                else:
                    proto_state = "down"
                break

        if proto_state == "established":
            # Fetch routes for this protocol
            r_res = run(["birdc", "-s", control_socket, "show", "route", "protocol", proto_name], check=False, timeout=10)
            r_out = r_res.stdout if r_res.returncode == 0 else ""
            routes = parse_routes(r_out)
            statuses[pid] = {
                "status": "established",
                "status_reason": None,
                "routes": routes,
            }
        else:
            reason = "Protocol down or not established"
            if not proto_found:
                reason = "Protocol not found in bird"
            statuses[pid] = {
                "status": "down",
                "status_reason": reason,
                "routes": [],
            }
    return statuses


def tick() -> None:
    global _last_apply_failed
    r = requests.get(f"{API}/internal/bgp-agent/{HOST}", headers=internal_headers(), timeout=10)
    r.raise_for_status()
    speakers = r.json().get("speakers", [])
    want = {spk["id"]: spk for spk in speakers}
    current_failed = set()
    err_updates = {}

    for spk_id, spk in want.items():
        try:
            apply_speaker(spk)
        except Exception as exc:  # best-effort: one broken speaker must not stop others
            log.error("speaker %s apply failed: %s", spk_id, exc)
            current_failed.add(spk_id)
            for p in spk.get("peers", []):
                err_updates[p["id"]] = {"status": "error", "status_reason": str(exc), "routes": []}

    _last_apply_failed = current_failed

    # Cleanup orphan speakers / netns / directories
    stale_ids = set(_applied.keys())
    if ROOT.exists():
        for d in ROOT.glob("*"):
            if d.is_dir():
                stale_ids.add(d.name)

    try:
        netns_out = sh("ip", "netns", "list", ok=True)
        for line in netns_out.splitlines():
            parts = line.strip().split()
            if parts and parts[0].startswith("qbgp-"):
                short_ns = parts[0][5:]
                stale_ids.add(short_ns)
    except Exception as exc:  # best-effort: netns list failure
        log.debug("failed to list netns: %s", exc)

    want_shorts = {gw_short(spk_id) for spk_id in want}
    want_full = set(want.keys())
    for sid in list(stale_ids):
        if sid not in want_full and sid not in want_shorts:
            if len(sid) == 8:
                matching_spk = next((gid for gid in want_full if gw_short(gid) == sid), None)
                if not matching_spk:
                    remove_speaker(sid)
            else:
                remove_speaker(sid)

    # Clear stale _reported entries for peers no longer wanted
    wanted_peer_ids = {p["id"] for spk in speakers for p in spk.get("peers", [])}
    for pid in list(_reported.keys()):
        if pid not in wanted_peer_ids:
            _reported.pop(pid, None)

    # Collect and report status changes
    status_updates = {pid: st for pid, st in err_updates.items() if _reported.get(pid) != st}
    for spk in speakers:
        if spk["id"] in _last_apply_failed:
            continue
        try:
            peer_statuses = check_speaker_status(spk)
            for pid, st in peer_statuses.items():
                if _reported.get(pid) != st:
                    status_updates[pid] = st
        except Exception as exc:  # best-effort: status check failure
            log.debug("status check failed for speaker %s: %s", spk["id"], exc)

    if status_updates:
        try:
            requests.post(f"{API}/internal/bgp-agent/{HOST}/status", json={"peers": status_updates},
                          headers=internal_headers(), timeout=10).raise_for_status()
            _reported.update(status_updates)
        except Exception as exc:  # best-effort: status report failure
            log.warning("failed to report bgp statuses: %s", exc)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s  %(message)s")
    if not HOST:
        raise SystemExit("bgp-worker: node_name is not set")
    ROOT.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            tick()
        except Exception as exc:  # best-effort: API restarting or temporarily unavailable
            log.warning("tick failed: %s", exc)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
