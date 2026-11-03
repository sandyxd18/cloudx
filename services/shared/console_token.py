"""Signed, short-lived tokens that let a browser open one server's VNC console through the websockify on its compute node.

compute-api mints the token (issue_token); the compute node's websockify plugin checks it (verify_token) and learns the
loopback VNC port. The key is derived from the shared AUTH_SECRET_KEY, so a console token (which lives in a plain-http
URL and has no jti to revoke) is not accepted as a login token by any API, and a login token is not accepted here.
"""
import hashlib
import hmac
import os
import time

from jose import JWTError, jwt

ALGORITHM = "HS256"
TOKEN_TYPE = "console"


KEY_PURPOSE = b"cloudx-console-token-v1"


def _secret() -> str:
    """The signing key, derived from AUTH_SECRET_KEY; a missing or blank one must fail closed, since a token signed with
    "" would verify against it."""
    secret = os.environ.get("AUTH_SECRET_KEY")
    if not secret:
        raise ValueError("AUTH_SECRET_KEY is not set")
    return hmac.new(secret.encode(), KEY_PURPOSE, hashlib.sha256).hexdigest()


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
