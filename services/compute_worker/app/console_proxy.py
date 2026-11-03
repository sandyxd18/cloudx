"""websockify token plugin for a compute node: `websockify --token-plugin console_proxy.ConsoleTokenPlugin ...`.

websockify loads this file as a top-level module, so it uses absolute imports only. It follows websockify's plugin
contract (constructed with the --token-source value, `lookup(token)` returns (host, port) or None) without importing
websockify.
"""
import os

from shared.config import load_service_config
from shared.console_token import verify_token

VNC_HOST = "127.0.0.1"  # libvirt binds every guest's VNC to loopback


class ConsoleTokenPlugin:
    def __init__(self, src):
        self.src = src
        load_service_config("compute-worker")  # this runs as its own systemd process: pick up AUTH_SECRET_KEY, COMPUTE_HOST

    def lookup(self, token):
        host = os.environ.get("COMPUTE_HOST")
        if not host:
            return None
        try:
            return VNC_HOST, verify_token(token, host)
        except Exception:  # best-effort: websockify cannot tolerate an exception from a plugin; any failure means no target
            return None
