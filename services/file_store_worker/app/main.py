"""file-store-worker: serves the NFS shares of this node.

Per share network it keeps:
  netns qfs-<id>   holding an OVS internal port on br-int whose iface-id is the share network's OVN port, so the port IP
                   (the mount target) answers from inside the namespace and OVN ACLs see the real client address;
  ganesha.nfsd     NFSv4 only, no rpcbind, bound to that IP, config rendered by shared.ganesha_cfg.
Per share: LV fs-<id> in the volume group, ext4, mounted under the mount root and exported at its UUID.
It polls file-store-api (/internal/fs-agent/<host>) and reconciles idempotently.
ponytail: poll every 5s; one ganesha per share network; a failed config keeps the previous one running.
"""
from __future__ import annotations

from shared.config import load_service_config

load_service_config("file-store-worker")

import hashlib
import logging
import os
import time
from pathlib import Path

import requests
from shared import catalog
from shared.ganesha_cfg import render
from shared.internal import internal_headers
from shared.proc import run

from .plan import plan

log = logging.getLogger("file-store-worker")
HOST = os.getenv("FILE_STORE_HOST") or os.getenv("NODE_NAME", "")
VG = os.getenv("FILE_STORE_VG", "vg-cloudx")
MOUNTS = Path(os.getenv("FILE_STORE_MOUNT_ROOT", "/var/lib/cloudx/fs"))
STATE = Path(os.getenv("FILE_STORE_STATE_DIR", "/var/lib/cloudx/fs-state"))
INTERVAL = int(os.getenv("FILE_STORE_WORKER_INTERVAL", "5"))

_applied: dict[str, str] = {}  # share network id -> sha of the config ganesha is running

def sh(*cmd: str, ok: bool = False) -> str:
    p = run(list(cmd), check=False, timeout=120)
    if p.returncode and not ok:
        raise RuntimeError(f"{' '.join(cmd[:4])} failed: {(p.stderr or p.stdout).strip()[:200]}")
    return p.stdout

def names(nid: str) -> tuple[str, str]:
    return f"qfs-{nid[:8]}", f"fs{nid[:8]}"

def lv(sid: str) -> str:
    return f"/dev/{VG}/fs-{sid}"

def ensure_netns(n: dict) -> None:
    ns, dev = names(n["id"])
    if ns not in sh("ip", "netns", "list", ok=True):
        sh("ip", "netns", "add", ns)
    sh("ovs-vsctl", "--may-exist", "add-port", "br-int", dev, "--", "set", "Interface", dev, "type=internal",
       f"external_ids:iface-id={n['port_name']}", f"external_ids:cloudx_fs={n['id']}")
    if run(["ip", "link", "show", dev], check=False).returncode == 0:  # still in the root namespace
        sh("ip", "link", "set", dev, "netns", ns)
    x = ["ip", "netns", "exec", ns, "ip"]
    sh(*x, "link", "set", dev, "address", n["mac"])
    sh(*x, "addr", "replace", f"{n['ip']}/{n['prefix']}", "dev", dev)
    sh(*x, "link", "set", dev, "up")
    sh(*x, "link", "set", "lo", "up")
    if n.get("gateway"):
        sh(*x, "route", "replace", "default", "via", n["gateway"])

def pid_of(d: Path) -> int | None:
    try:
        pid = int((d / "ganesha.pid").read_text().split()[0])
        return pid if Path(f"/proc/{pid}").exists() else None
    except (OSError, ValueError, IndexError):
        return None

def apply_ganesha(n: dict, shares: list[dict]) -> None:
    """Write atomically, then start or reload (SIGHUP). ganesha has no parse-only mode; the config is rendered only from
    validated CIDRs, levels and UUIDs, and a rejected reload leaves the running exports untouched."""
    d = STATE / n["id"]
    d.mkdir(parents=True, exist_ok=True)
    cfg = render(n["ip"], [{"id": s["id"], "path": str(MOUNTS / s["id"]), "rules": s["rules"]} for s in shares])
    digest = hashlib.sha256(cfg.encode()).hexdigest()
    pid = pid_of(d)
    if _applied.get(n["id"]) == digest and pid:
        return
    new = d / "ganesha.conf.new"
    new.write_text(cfg)
    new.replace(d / "ganesha.conf")
    if pid:
        sh("kill", "-HUP", str(pid))
    else:
        ns = names(n["id"])[0]
        sh("ip", "netns", "exec", ns, "ganesha.nfsd", "-f", str(d / "ganesha.conf"), "-p", str(d / "ganesha.pid"),
           "-L", str(d / "ganesha.log"), "-N", "NIV_EVENT")
    _applied[n["id"]] = digest
    log.info("share network %s: ganesha %s", n["id"], "reloaded" if pid else "started")

def stop_network(nid: str) -> None:
    d = STATE / nid
    pid = pid_of(d)
    if pid:
        sh("kill", str(pid), ok=True)
    ns, dev = names(nid)
    sh("ovs-vsctl", "--if-exists", "del-port", "br-int", dev, ok=True)
    sh("ip", "netns", "del", ns, ok=True)
    if d.exists():
        for f in d.glob("*"):
            f.unlink(missing_ok=True)
        d.rmdir()
    _applied.pop(nid, None)

def have() -> dict[str, dict]:
    # -S, not a positional VG: cloudx.conf's lvm_vg_name lands in the environment as LVM_VG_NAME, which LVM reads as its
    # default VG and then takes a bare "vg-cloudx" argument for an LV name.
    out = sh("lvs", "--noheadings", "--nosuffix", "--units", "g", "-o", "lv_name,lv_size", "-S", f"vg_name={VG}", ok=True)
    res = {}
    for ln in out.splitlines():
        f = ln.split()
        if len(f) == 2 and f[0].startswith("fs-"):
            res[f[0][3:]] = {"size_gb": int(float(f[1]))}
    return res

def ensure_mounted(sid: str) -> None:
    m = MOUNTS / sid
    m.mkdir(parents=True, exist_ok=True)
    if run(["findmnt", "-n", str(m)], check=False).returncode:
        sh("mount", lv(sid), str(m))
        m.chmod(0o777)  # ponytail: 0777 root, access is enforced by IP rules + Root_Squash; per-share UID mapping if needed

def do(act: tuple, want: dict[str, dict]) -> str:
    """Run one action; returns the status to report."""
    kind, sid = act[0], act[1]
    if kind == "create":
        sh("lvcreate", "-y", "-L", f"{want[sid]['size_gb']}G", "-n", f"fs-{sid}", VG)
        sh("mkfs.ext4", "-q", lv(sid))
        ensure_mounted(sid)
        return "available"
    if kind == "extend":
        sh("lvextend", "-L", f"{act[2]}G", lv(sid))
        sh("resize2fs", lv(sid))
        return "available"
    m = MOUNTS / sid  # delete
    if run(["findmnt", "-n", str(m)], check=False).returncode == 0:
        sh("umount", str(m))
    if Path(lv(sid)).exists():
        sh("lvremove", "-f", lv(sid))
    if m.exists():
        m.rmdir()
    return "deleted"

def reconcile_network(n: dict, shares: list[dict], report: dict) -> None:
    if n["status"] == "deleting":
        if shares:
            return
        stop_network(n["id"])
        report["share_networks"][n["id"]] = "deleted"
        return
    if n["status"] == "error":
        return
    ensure_netns(n)
    present = have()
    want = {s["id"]: s for s in shares if s["status"] in ("creating", "extending", "deleting") or s["id"] in present}
    for act in plan(want, present):
        try:
            report["shares"][act[1]] = do(act, want)
        except Exception as exc:  # best-effort: one broken share must not stop the others
            log.error("share %s %s: %s", act[1], act[0], exc)
            report["shares"][act[1]] = "error"
    present = have()
    serving = []
    for s in shares:
        if s["id"] in present and report["shares"].get(s["id"]) not in ("deleted", "error") and s["status"] != "deleting":
            ensure_mounted(s["id"])
            serving.append(s)
    try:
        apply_ganesha(n, serving)
    except RuntimeError as exc:
        log.error("share network %s: ganesha not applied: %s", n["id"], exc)
        return
    if n["status"] == "creating":
        report["share_networks"][n["id"]] = "active"

def tick() -> None:
    api = catalog.url("file-store").rstrip("/")
    r = requests.get(f"{api}/internal/fs-agent/{HOST}", headers=internal_headers(), timeout=10)
    r.raise_for_status()
    state = r.json()
    report: dict = {"share_networks": {}, "shares": {}}
    for n in state["share_networks"]:
        mine = [s for s in state["shares"] if s["share_network_id"] == n["id"]]
        try:
            reconcile_network(n, mine, report)
        except Exception as exc:  # best-effort: one broken share network must not stop the others
            log.error("share network %s: %s", n["id"], exc)
            if n["status"] == "creating":
                report["share_networks"][n["id"]] = "error"
    report["shares"] = {k: v for k, v in report["shares"].items()
                        if next((s["status"] for s in state["shares"] if s["id"] == k), None) != v}
    if report["shares"] or report["share_networks"]:
        requests.post(f"{api}/internal/fs-agent/{HOST}/report", json=report, headers=internal_headers(),
                      timeout=10).raise_for_status()

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s  %(message)s")
    if not HOST:
        raise SystemExit("file-store-worker: file_store_host is not set")
    STATE.mkdir(parents=True, exist_ok=True)
    MOUNTS.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            tick()
        except Exception as exc:  # best-effort: API restarting or briefly unreachable; retry next tick
            log.warning("tick failed: %s", exc)
        time.sleep(INTERVAL)

if __name__ == "__main__":
    main()
