"""Signed, short-lived tokens that let a browser open one server's VNC console through the websockify on its compute node.

compute-api mints the token (issue_token); the compute node's websockify plugin checks it (verify_token) and learns the
loopback VNC port. The key is the shared AUTH_SECRET_KEY, so `typ` keeps an ordinary auth-api session token from being
accepted here.
"""
import os
import time

from jose import JWTError, jwt

ALGORITHM = "HS256"
TOKEN_TYPE = "console"


def _secret() -> str:
    return os.environ["AUTH_SECRET_KEY"]


def issue_token(server_id: str, host: str, vnc_port: int, ttl: int = 300) -> str:
    claims = {"typ": TOKEN_TYPE, "sid": server_id, "host": host, "port": vnc_port, "exp": int(time.time()) + ttl}
    return jwt.encode(claims, _secret(), algorithm=ALGORITHM)


def verify_token(token: str, host: str) -> int:
    """Return the VNC port the token grants on `host`; ValueError if it is forged, expired or meant for anything else."""
    try:
        claims = jwt.decode(token, _secret(), algorithms=[ALGORITHM], options={"require_exp": True})
    except JWTError as e:
        raise ValueError(f"invalid console token: {e}") from e
    if claims.get("typ") != TOKEN_TYPE:
        raise ValueError("not a console token")
    if claims.get("host") != host:
        raise ValueError("console token is for another host")
    port = claims.get("port")
    if not isinstance(port, int) or isinstance(port, bool):
        raise ValueError("console token has no port")
    return port
