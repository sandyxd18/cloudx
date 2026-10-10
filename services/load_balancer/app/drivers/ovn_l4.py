"""
L4 Network Load Balancer driver backed by OVN's native load balancer.

One OVN Load_Balancer per listener (named cloudx-ls-<listener id>, tagged external_ids:cloudx_lb=<lb id>), attached to
the VIP's logical switch. OVN balances by hash: SOURCE_IP pins a client to a backend (selection_fields=ip_src); for
ROUND_ROBIN / LEAST_CONNECTIONS OVN has nothing finer, so connections are spread by 5-tuple hash. Use an ALB for those.
ponytail: delete + re-add on every change (a flap of a few ms); in-place `set vips` if that ever matters.
"""
from __future__ import annotations

import logging
import os

from shared.proc import run

from .base import LoadBalancerDriver

log = logging.getLogger("lb-driver.ovn-l4")
NB_DB = os.getenv("OVN_NB_DB", "tcp:127.0.0.1:6641")
SB_DB = os.getenv("OVN_SB_DB", "tcp:127.0.0.1:6642")


def _nb(*args: str, check: bool = True) -> str:
    p = run(["ovn-nbctl", f"--db={NB_DB}", "--timeout=8", *args], check=False, timeout=20)
    if check and p.returncode != 0:
        raise RuntimeError(f"ovn-nbctl {' '.join(args[:2])} failed: {p.stderr.strip()[:200]}")
    return p.stdout.strip()


def service_monitor_status() -> dict[tuple[str, int], str]:
    """(backend ip, port) -> online|offline|error, as measured by ovn-controller's health checks."""
    p = run(["ovn-sbctl", f"--db={SB_DB}", "--timeout=8", "--bare", "--columns=ip,port,status", "find", "service_monitor"],
            check=False, timeout=20)
    rows = [r.split("\n") for r in p.stdout.strip().split("\n\n") if r.strip()]
    return {(r[0], int(r[1])): r[2] for r in rows if len(r) == 3 and r[2] in ("online", "offline", "error")}


def _name(listener_id: str) -> str:
    return f"cloudx-ls-{listener_id}"


class OVNL4Driver(LoadBalancerDriver):
    name = "ovn_l4"
    layer = "L4"

    def __init__(self, config: dict) -> None:
        pass

    def _existing(self, lb_id: str) -> list[str]:
        out = _nb("--bare", "--columns=name", "find", "load_balancer", f"external_ids:cloudx_lb={lb_id}")
        return [n for n in out.split() if n]

    def _attach(self, switch: str, lb_name: str) -> None:
        """Like ovn-octavia-provider: LB on the VIP switch, on every router joined to it, and on each of that router's
        non-gateway switches (the reply is un-DNATed by conntrack on the client's own switch; lr_out_undnat only covers the
        gateway port)."""
        short = lambda s: s.split("(")[-1].rstrip(")")  # noqa: E731  "uuid (name)" -> name
        lsp_switch = {short(p): short(s) for s in _nb("ls-list").splitlines() for p in _nb("lsp-list", short(s)).splitlines()}
        _nb("--may-exist", "ls-lb-add", switch, lb_name)
        lrp_of = lambda lsp: _nb("get", "logical_switch_port", lsp, "options:router-port", check=False).strip('"')  # noqa: E731
        vip_lrps = {lrp_of(lsp) for lsp, sw in lsp_switch.items() if sw == switch}
        for r in map(short, _nb("lr-list").splitlines()):
            lrps = [short(p) for p in _nb("lrp-list", r).splitlines()]
            if not vip_lrps & set(lrps):
                continue
            _nb("--may-exist", "lr-lb-add", r, lb_name)
            for lsp, sw in lsp_switch.items():
                lrp = lrp_of(lsp)
                if lrp in lrps and _nb("get", "logical_router_port", lrp, "gateway_chassis") == "[]" \
                        and _nb("get", "logical_router_port", lrp, "ha_chassis_group") == "[]":
                    _nb("--may-exist", "ls-lb-add", sw, lb_name)
        # ponytail: networks joined to the router after the last apply are covered on the next apply
    def apply(self, lb: dict, listeners: list[dict]) -> None:
        want = {}
        for ls in listeners:
            pool = next((p for p in ls["pools"] if p["id"] == ls["default_pool_id"]), None)
            backends = ",".join(f"{m['address']}:{m['protocol_port']}" for m in (pool or {"members": []})["members"]
                                if m["weight"] > 0)
            if backends:  # OVN refuses a VIP with no backend
                want[_name(ls["id"])] = (ls, pool, backends)
        for name in self._existing(lb["id"]):
            _nb("--if-exists", "lb-del", name)
        for name, (ls, pool, backends) in want.items():
            _nb("lb-add", name, f"{lb['vip_address']}:{ls['protocol_port']}", backends, "udp" if ls["protocol"] == "UDP" else "tcp")
            _nb("set", "load_balancer", name, f"external_ids:cloudx_lb={lb['id']}")
            if pool["lb_algorithm"] == "SOURCE_IP":
                _nb("set", "load_balancer", name, "selection_fields=ip_src")
            hm = pool.get("health_monitor")
            if hm and hm["type"] in ("TCP", "HTTP"):
                vip = f"{lb['vip_address']}:{ls['protocol_port']}"
                _nb("--id=@hc", "create", "Load_Balancer_Health_Check", f'vip="{vip}"', f"options:interval={hm['delay']}",
                    f"options:timeout={hm['timeout']}", f"options:failure_count={hm['max_retries']}",
                    "--", "add", "Load_Balancer", name, "health_check", "@hc")
                for m in pool["members"]:
                    if m.get("port_name"):  # source ip = the VIP: ovn-controller probes from there
                        _nb("set", "load_balancer", name, f'ip_port_mappings:{m["address"]}={m["port_name"]}:{lb["vip_address"]}')
            if lb.get("vip_switch"):
                self._attach(lb["vip_switch"], name)

    def delete(self, lb: dict) -> None:
        for name in self._existing(lb["id"]):
            _nb("--if-exists", "lb-del", name)

    def health(self) -> dict:
        try:
            _nb("show")
            return {"ok": True}
        except Exception as exc:  # best-effort: report, never raise
            return {"ok": False, "error": str(exc)}
