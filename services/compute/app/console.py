"""The browser console link: a noVNC page served by the websockify on the server's own compute node."""
from shared import console_token

from .config import log

PROXY_PORT = 6080
PROXY_SCHEME = "http"


def novnc_url(server_id: str, vnc: dict) -> str | None:
    """Link to the node's websockify with a token for this server's VNC port, or None when the worker did not say where
    the console is (no VNC port, no node) or no token can be signed. `vnc` is the compute worker's get_console_info reply."""
    host, proxy_host, port = vnc.get("host"), vnc.get("proxy_host"), vnc.get("port")
    if not (host and proxy_host and port):
        return None
    try:
        token = console_token.issue_token(server_id, host, port)
    except ValueError as exc:
        log.warning("no console link for %s: %s", server_id, exc)
        return None
    return f"{PROXY_SCHEME}://{proxy_host}:{PROXY_PORT}/vnc.html?autoconnect=1&path=websockify%3Ftoken={token}"
