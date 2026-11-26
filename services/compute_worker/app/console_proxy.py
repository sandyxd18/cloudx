"""websockify token plugin for a compute node: `websockify --token-plugin console_proxy.ConsoleTokenPlugin ...`.

websockify loads this file as a top-level module, so it uses absolute imports only. It follows websockify's plugin
contract (constructed with the --token-source value, `lookup(token)` returns (host, port) or None) without importing
websockify.
"""
import logging
import os
import sys
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
        if graphics is not None and graphics.attrib.get("port") == str(port):
            return True
        log.warning("console refused: domain %s no longer owns VNC port %s", domain, port)
        return False
    except Exception as exc:  # best-effort: websockify cannot tolerate an exception from a plugin; any failure means no target
        log.warning("console refused: cannot confirm that domain %s owns port %s: %s", domain, port, exc)
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # best-effort: the answer is already decided; a failed close must not change it
                pass


WEB_DIR = "/usr/share/novnc"   # the noVNC web client (the novnc apt package)
PROXY_PORT = "6080"            # keep equal to PROXY_PORT of compute-api's console.py: it is the port in the link


def tls_files() -> tuple[str, str] | None:
    """(certificate, key) when `console_proxy_cert` and `console_proxy_key` are both set, None when neither is (the proxy
    serves plain http, as before). One of the two alone is a mistake: ValueError, so the proxy does not start half configured.
    Read from the environment, which load_service_config fills from compute-worker.conf."""
    cert = os.environ.get("CONSOLE_PROXY_CERT", "").strip()
    key = os.environ.get("CONSOLE_PROXY_KEY", "").strip()
    if cert and key:
        return cert, key
    if cert or key:
        missing = "console_proxy_key" if cert else "console_proxy_cert"
        raise ValueError(f"{missing} is not set: console_proxy_cert and console_proxy_key go together")
    return None


def proxy_args(tls: tuple[str, str] | None) -> list[str]:
    """Arguments of websockify. With a certificate it serves https only: the console token is in the link, and a plain
    http connection to the same port would send it in the clear."""
    args = ["--web", WEB_DIR, "--token-plugin", "console_proxy.ConsoleTokenPlugin"]
    if tls:
        args += ["--cert", tls[0], "--key", tls[1], "--ssl-only"]
    return [*args, PROXY_PORT]


def _run(args: list[str]) -> None:
    from websockify.websocketproxy import websockify_init  # imported here: the plugin and tls_files() work without websockify

    sys.argv = ["websockify", *args]
    websockify_init()


def main() -> None:
    """Entry point of the `cloudx-novnc-proxy` unit (`python -m console_proxy`): websockify, with TLS when it is configured.
    A setting that does not make a working https proxy ends the process with the reason (see `journalctl -u cloudx-novnc-proxy`)
    instead of serving http under a link that says https."""
    load_service_config("compute-worker")
    try:
        tls = tls_files()
    except ValueError as exc:
        sys.exit(f"console proxy not started: {exc}")
    for path in tls or ():
        if not (os.path.isfile(path) and os.access(path, os.R_OK)):
            sys.exit(f"console proxy not started: {path} is not a readable file (console_proxy_cert / console_proxy_key)")
    _run(proxy_args(tls))


def _compute_host() -> str:
    return os.environ.get("COMPUTE_HOST", os.environ.get("NODE_NAME", DEFAULT_COMPUTE_HOST))  # same chain as the worker's config.py


class ConsoleTokenPlugin:
    """Every refusal is logged (logger `console-proxy`, shown by websockify on stderr, so `journalctl -u
    cloudx-novnc-proxy`) with its reason and never the token."""

    def __init__(self, src):
        self.src = src
        load_service_config("compute-worker")  # this runs as its own systemd process: pick up AUTH_SECRET_KEY, COMPUTE_HOST
        if not os.environ.get("AUTH_SECRET_KEY"):
            log.warning("AUTH_SECRET_KEY is not set: every console token will be refused")
        if not _compute_host():
            log.warning("COMPUTE_HOST is empty: every console token will be refused")

    def lookup(self, token):
        host = _compute_host()
        if not host:
            log.warning("console refused: COMPUTE_HOST is empty")
            return None
        try:
            claims = verify_claims(token, host)
            domain = claims.get("dom")
            if not domain:
                log.warning("console refused: the token names no libvirt domain")
                return None
            if not _domain_owns_port(domain, claims["port"]):    # logs its own reason
                return None
            return VNC_HOST, claims["port"]
        except Exception as exc:  # best-effort: websockify cannot tolerate an exception from a plugin; any failure means no target
            log.warning("console refused: %s", str(exc).replace(token, "<token>"))    # never the token itself
            return None


if __name__ == "__main__":
    main()
