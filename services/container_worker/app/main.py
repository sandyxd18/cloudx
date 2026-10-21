"""container-worker: runs the containers of this node with Docker (network none) and plumbs each one to its OVN port.

Per container: docker container ct-<id>; netns ct<id8> attached to its PID holding an OVS internal port on br-int whose
iface-id is the container's OVN port. Polls container-api (/internal/ct-agent/<host>) and reconciles idempotently.
ponytail: poll every 5s; a failed create is not retried until the wanted state changes; worker restart re-plumbs ports.
"""
from __future__ import annotations

from shared.config import load_service_config

load_service_config("container-worker")

import logging
import os
import time
from pathlib import Path

import requests
from shared import catalog
from shared.internal import internal_headers
from shared.proc import run

from .plan import plan

log = logging.getLogger("container-worker")
HOST = os.getenv("CONTAINER_HOST") or os.getenv("NODE_NAME", "")
STATE = Path(os.getenv("CONTAINER_STATE_DIR", "/var/lib/cloudx/ct-state"))
INTERVAL = int(os.getenv("CONTAINER_WORKER_INTERVAL", "5"))
LOG_CAP = 16 * 1024
CAPS = ("CHOWN", "DAC_OVERRIDE", "FOWNER", "SETUID", "SETGID", "NET_BIND_SERVICE")  # minimum for common images (nginx)

_pids: dict[str, str] = {}    # container id -> docker PID the netns was attached to
_failed: dict[str, tuple] = {}  # container id -> (want, error) of a failed action

def sh(*cmd: str, ok: bool = False, timeout: int = 120) -> str:
    p = run(list(cmd), check=False, timeout=timeout)
    if p.returncode and not ok:
        raise RuntimeError(f"{' '.join(cmd[:3])} failed: {(p.stderr or p.stdout).strip()[:300]}")
    return p.stdout

def logs_of(cid: str) -> str:
    p = run(["docker", "logs", "--tail", "200", names(cid)[0]], check=False, timeout=30)  # docker replays the container's stderr on ours
    return (p.stdout or "") + (p.stderr or "")

def names(cid: str) -> tuple[str, str]:
    return f"ct-{cid}", f"ct{cid[:8]}"

def docker_state() -> dict[str, str]:
    out = sh("docker", "ps", "-a", "--filter", "name=^ct-", "--format", "{{.Names}} {{.State}}")
    return {n[3:]: s for n, s in (ln.split() for ln in out.splitlines() if len(ln.split()) == 2)}

def create(c: dict) -> None:
    name, _ = names(c["id"])
    d = STATE / c["id"]
    d.mkdir(parents=True, exist_ok=True)
    (d / "resolv.conf").write_text("".join(f"nameserver {s}\n" for s in c["dns"]))
    args = ["docker", "create", "--name", name, "--network", "none", "--cpus", str(c["cpu"]), "--memory", f"{c['memory_mb']}m",
            "--pids-limit", "256", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "-v", f"{d / 'resolv.conf'}:/etc/resolv.conf:ro"]
    if c.get("data_path"):
        args += ["-v", f"{name}-data:{c['data_path']}"]
    for k in CAPS:
        args += ["--cap-add", k]
    for k, v in c["env"].items():
        args += ["-e", f"{k}={v}"]
    sh(*args, "--", c["image"], *c["command"], timeout=900)  # includes the image pull

def plumb(c: dict) -> None:
    name, dev = names(c["id"])
    pid = sh("docker", "inspect", "-f", "{{.State.Pid}}", name).strip()
    if pid in ("", "0") or _pids.get(c["id"]) == pid:
        return
    sh("ovs-vsctl", "--if-exists", "del-port", "br-int", dev, ok=True)
    sh("ip", "netns", "del", dev, ok=True)
    sh("ip", "netns", "attach", dev, pid)
    sh("ovs-vsctl", "--may-exist", "add-port", "br-int", dev, "--", "set", "Interface", dev, "type=internal",
       f"external_ids:iface-id={c['port_name']}", f"external_ids:cloudx_ct={c['id']}")
    sh("ip", "link", "set", dev, "netns", dev)
    x = ["ip", "netns", "exec", dev, "ip"]
    sh(*x, "link", "set", dev, "address", c["mac"])
    sh(*x, "addr", "replace", f"{c['ip']}/{c['prefix']}", "dev", dev)
    sh(*x, "link", "set", dev, "up")
    sh(*x, "link", "set", "lo", "up")
    if c.get("gateway"):
        sh(*x, "route", "replace", "default", "via", c["gateway"])
    _pids[c["id"]] = pid

def remove(cid: str) -> None:
    name, dev = names(cid)
    sh("docker", "rm", "-f", "-v", name, ok=True)
    sh("docker", "volume", "rm", "-f", f"{name}-data", ok=True)
    sh("ovs-vsctl", "--if-exists", "del-port", "br-int", dev, ok=True)
    sh("ip", "netns", "del", dev, ok=True)
    d = STATE / cid
    for f in d.glob("*"):
        f.unlink(missing_ok=True)
    if d.exists():
        d.rmdir()
    _pids.pop(cid, None)
    _failed.pop(cid, None)

def tick() -> None:
    api = catalog.url("container").rstrip("/")
    r = requests.get(f"{api}/internal/ct-agent/{HOST}", headers=internal_headers(), timeout=10)
    r.raise_for_status()
    want = {c["id"]: c for c in r.json()["containers"]}
    have = docker_state()
    report: dict = {}
    for cid in list(_failed):
        if cid not in want or _failed[cid][0] != want[cid]["want"]:
            _failed.pop(cid)
    for act in plan(want, have):
        kind, cid = act
        c = want[cid]
        if cid in _failed and kind != "delete":
            continue
        try:
            if kind == "delete":
                remove(cid)
                report[cid] = {"state": "deleted"}
            elif kind == "create":
                create(c)
                sh("docker", "start", names(cid)[0])
            elif kind == "start":
                sh("docker", "start", names(cid)[0])
            elif kind == "restart":
                sh("docker", "restart", "-t", "10", names(cid)[0], timeout=60)
            elif kind == "stop":
                sh("docker", "stop", "-t", "10", names(cid)[0], timeout=60)
        except Exception as exc:  # best-effort: one broken container must not stop the others
            log.error("container %s %s: %s", cid, kind, exc)
            _failed[cid] = (c["want"], str(exc)[:300])
    have = docker_state()
    for cid, c in want.items():
        if c["want"] == "deleting" or cid in report:
            continue
        if cid in _failed:
            report[cid] = {"state": "error", "error": _failed[cid][1]}
            continue
        st = have.get(cid)
        if st == "running":
            try:
                plumb(c)
                logs = logs_of(cid)
                report[cid] = {"state": "running", "logs": logs[-LOG_CAP:]}
            except RuntimeError as exc:
                log.error("container %s plumb: %s", cid, exc)
                report[cid] = {"state": "error", "error": str(exc)[:300]}
        elif st:
            _pids.pop(cid, None)
            report[cid] = {"state": "stopped", "logs": logs_of(cid)[-LOG_CAP:]}
    if report:
        requests.post(f"{api}/internal/ct-agent/{HOST}/report", json={"containers": report}, headers=internal_headers(),
                      timeout=10).raise_for_status()

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s  %(message)s")
    if not HOST:
        raise SystemExit("container-worker: container_host is not set")
    STATE.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            tick()
        except Exception as exc:  # best-effort: API restarting or briefly unreachable; retry next tick
            log.warning("tick failed: %s", exc)
        time.sleep(INTERVAL)

if __name__ == "__main__":
    main()