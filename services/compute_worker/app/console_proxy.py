"""websockify token plugin for a compute node: `websockify --token-plugin console_proxy.ConsoleTokenPlugin ...`.

websockify loads this file as a top-level module, so it uses absolute imports only. It follows websockify's plugin
contract (constructed with the --token-source value, `lookup(token)` returns (host, port) or None) without importing
websockify.
"""
import logging
import os
import xml.etree.ElementTree as ET

from shared.config import load_service_config
from shared.console_token import verify_claims

log = logging.getLogger("console-proxy")

VNC_HOST = "127.0.0.1"  # libvirt binds every guest's VNC to loopback
DEFAULT_COMPUTE_HOST = "cloudx-compute-01"  # keep equal to the fallback of COMPUTE_HOST in config.py: the token's host comes from there


def _domain_owns_port(domain: str, port: int) -> bool:
    """True only if libvirt says `domain` currently has its VNC graphics on `port`. A token outlives the VM it was minted
    for (300 s), and libvirt hands a freed port to the next VM, so the token alone must not decide which screen opens.
    Any error means False."""
    conn = None
    try:
        import libvirt  # imported here: the plugin must still load (and refuse) on a node where the binding is broken
        conn = libvirt.open(os.environ.get("LIBVIRT_URI", "qemu:///system"))   # same variable and default as the worker's config.py
        root = ET.fromstring(conn.lookupByName(domain).XMLDesc(0))
        graphics = root.find("./devices/graphics[@type='vnc']")
        return graphics is not None and graphics.attrib.get("port") == str(port)
    except Exception as exc:  # best-effort: websockify cannot tolerate an exception from a plugin; any failure means no target
        log.warning("console refused: cannot confirm that domain %s owns port %s: %s", domain, port, exc)
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # best-effort: the answer is already decided; a failed close must not change it
                pass


class ConsoleTokenPlugin:
    def __init__(self, src):
        self.src = src
        load_service_config("compute-worker")  # this runs as its own systemd process: pick up AUTH_SECRET_KEY, COMPUTE_HOST

    def lookup(self, token):
        host = os.environ.get("COMPUTE_HOST", os.environ.get("NODE_NAME", DEFAULT_COMPUTE_HOST))  # same chain as the worker's config.py
        if not host:
            return None
        try:
            claims = verify_claims(token, host)
            domain = claims.get("dom")
            if not domain:
                return None
            if not _domain_owns_port(domain, claims["port"]):
                return None
            return VNC_HOST, claims["port"]
        except Exception:  # best-effort: websockify cannot tolerate an exception from a plugin; any failure means no target
            return None
