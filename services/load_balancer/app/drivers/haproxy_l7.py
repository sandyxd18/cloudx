"""
L7 Application Load Balancer driver backed by HAProxy.

Generates one HAProxy config per Load Balancer and reloads HAProxy.
ponytail: production would use a dynamic socket/API; here we generate
          config files under a configurable root and SIGHUP haproxy.
"""
from __future__ import annotations

import logging
import os
import signal
from pathlib import Path

from .base import LoadBalancerDriver

log = logging.getLogger("lb-driver.haproxy-l7")


_HAPROXY_CFG_TEMPLATE = """
global
    daemon

defaults
    mode http
    timeout connect 5s
    timeout client 30s
    timeout server 30s
    option forwardfor

frontend fe_{lb_id}
    bind {vip}:{listener_port} {bind_opts}
    default_backend be_{lb_id}_{pool_id}

backend be_{lb_id}_{pool_id}
    balance {balance}
    option httpchk GET {health_path}
{members}
"""


def _safe(value, what: str) -> str:
    """Reject anything that could break out of a haproxy.cfg token (whitespace, control chars)."""
    s = str(value)
    if not s or any(c.isspace() or not c.isprintable() for c in s):
        raise ValueError(f"unsafe {what} for haproxy config")
    return s


class HAProxyL7Driver(LoadBalancerDriver):
    name = "haproxy_l7"
    layer = "L7"

    def __init__(self, config: dict) -> None:
        self.config_root = Path(config.get("config_root", "/etc/haproxy/cloudx-lb"))
        self.config_root.mkdir(parents=True, exist_ok=True)
        self.pid_file = Path(config.get("pid_file", "/run/haproxy.pid"))
        self.haproxy_bin = config.get("haproxy_bin", "haproxy")

    def _config_path(self, lb_id: str) -> Path:
        return self.config_root / f"lb-{lb_id}.cfg"

    def _reload(self):
        if self.pid_file.exists():
            try:
                pid = int(self.pid_file.read_text().strip())
                os.kill(pid, signal.SIGHUP)
                return
            except Exception as exc:
                log.warning("Could not SIGHUP haproxy: %s", exc)
        log.info("No haproxy PID found; config written but not reloaded.")

    def _balance(self, algorithm: str | None) -> str:
        return {
            "LEAST_CONNECTIONS": "leastconn",
            "SOURCE_IP": "source",
        }.get(algorithm or "ROUND_ROBIN", "roundrobin")

    def create_load_balancer(self, lb: dict) -> None:
        # HAProxy config is created lazily when listener/pool exist.
        pass

    def delete_load_balancer(self, lb: dict) -> None:
        path = self._config_path(lb["id"])
        if path.exists():
            path.unlink()
        self._reload()

    def apply_listener(self, lb: dict, listener: dict) -> None:
        self._render(lb, listener)

    def delete_listener(self, lb: dict, listener: dict) -> None:
        self._render(lb, listener)

    def apply_pool(self, lb: dict, listener: dict, pool: dict, members: list[dict]) -> None:
        self._render(lb, listener, pool, members)

    def delete_pool(self, lb: dict, pool: dict) -> None:
        pass

    def update_members(self, lb: dict, listener: dict, pool: dict, members: list[dict]) -> None:
        self._render(lb, listener, pool, members)

    def _render(
        self,
        lb: dict,
        listener: dict | None = None,
        pool: dict | None = None,
        members: list[dict] | None = None,
    ) -> None:
        if listener is None or pool is None:
            return
        members = members or []
        health_path = "/"
        for hm in listener.get("health_monitors", []):
            health_path = _safe(hm.get("url_path", "/"), "url_path")

        member_lines = "\n".join(
            f"    server member_{_safe(m['id'], 'member id')} {_safe(m['address'], 'member address')}:{int(m['protocol_port'])} check"
            for m in members
            if m.get("operating_status", "ONLINE") == "ONLINE"
        )
        if not member_lines:
            member_lines = "    server placeholder 127.0.0.1:1 check disabled"

        bind_opts = ""
        if listener.get("protocol") == "HTTPS" and listener.get("ssl_cert_path"):
            bind_opts = f"ssl crt {_safe(listener['ssl_cert_path'], 'ssl_cert_path')}"

        cfg = _HAPROXY_CFG_TEMPLATE.format(
            lb_id=_safe(lb["id"], "lb id"),
            vip=_safe(lb["vip_address"], "vip"),
            listener_port=int(listener["protocol_port"]),
            bind_opts=bind_opts,
            pool_id=_safe(pool["id"], "pool id"),
            balance=self._balance(pool.get("lb_algorithm")),
            health_path=health_path,
            members=member_lines,
        )
        self._config_path(lb["id"]).write_text(cfg)
        self._reload()

    def health(self) -> dict:
        return {"ok": True}
