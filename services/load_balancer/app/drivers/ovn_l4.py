"""
L4 Network Load Balancer driver backed by OVN native load-balancer.

Uses ovn-nbctl to manage IPv4/IPv6 virtual-ips with backends.
ponytail: production-grade might use OVN Northbound DB via python-ovs
          instead of subprocess; this is sufficient for the lab.
"""
from __future__ import annotations

import logging
import subprocess

from .base import LoadBalancerDriver

log = logging.getLogger("lb-driver.ovn-l4")


class OVNL4Driver(LoadBalancerDriver):
    name = "ovn_l4"
    layer = "L4"

    def __init__(self, config: dict) -> None:
        self.nb_db = config.get("nb_db", "unix:/var/run/ovn/ovnnb_db.sock")
        self.ovn_nbctl = config.get("ovn_nbctl", "ovn-nbctl")
        self.logical_router = config.get("logical_router", "cloudx_router")

    def _run(self, *args):
        cmd = [self.ovn_nbctl, f"--db={self.nb_db}"] + list(args)
        log.debug("Running: %s", " ".join(cmd))
        subprocess.run(cmd, check=True, capture_output=True, text=True)

    def _lb_name(self, lb_id: str) -> str:
        return f"cloudx-lb-{lb_id}"

    def _format_vips(self, vip_address: str, protocol_port: int, members: list[dict]) -> str:
        backends = ",".join(
            f"{m['address']}:{m['protocol_port']}"
            for m in members
            if m.get("operating_status", "ONLINE") == "ONLINE"
        )
        if not backends:
            backends = ""
        return f"{vip_address}:{protocol_port}={backends}"

    def create_load_balancer(self, lb: dict) -> None:
        name = self._lb_name(lb["id"])
        try:
            self._run("lb-add", name, "0.0.0.0:0=", "tcp")
            self._run("lr-lb-add", self.logical_router, name)
        except Exception as exc:
            log.error("Failed to create OVN LB %s: %s", name, exc)
            raise

    def delete_load_balancer(self, lb: dict) -> None:
        name = self._lb_name(lb["id"])
        try:
            self._run("lr-lb-del", self.logical_router, name)
        except Exception:
            pass
        try:
            self._run("lb-del", name)
        except Exception:
            pass

    def apply_listener(self, lb: dict, listener: dict) -> None:
        name = self._lb_name(lb["id"])
        protocol = listener.get("protocol", "TCP").lower()
        port = listener["protocol_port"]
        vips = self._format_vips(lb["vip_address"], port, listener.get("members", []))
        self._run("lb-add", name, vips, protocol)

    def delete_listener(self, lb: dict, listener: dict) -> None:
        pass  # OVN LB is replaced atomically via lb-add; no-op.

    def apply_pool(self, lb: dict, listener: dict, pool: dict, members: list[dict]) -> None:
        self.update_members(lb, listener, pool, members)

    def delete_pool(self, lb: dict, pool: dict) -> None:
        pass

    def update_members(self, lb: dict, listener: dict, pool: dict, members: list[dict]) -> None:
        name = self._lb_name(lb["id"])
        protocol = listener.get("protocol", "TCP").lower()
        port = listener["protocol_port"]
        vips = self._format_vips(lb["vip_address"], port, members)
        options = f"event=disabled;selection_fields=ip_src" if pool.get("lb_algorithm") == "SOURCE_IP" else ""
        self._run("lb-set-options", name, options)
        self._run("lb-add", name, vips, protocol)

    def health(self) -> dict:
        try:
            self._run("show")
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
