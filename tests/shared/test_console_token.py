"""A console token is the only thing that lets a browser reach a VNC port: it names one server on one compute host,
is signed with the shared secret, and expires quickly."""
import os
import time

import pytest
from jose import jwt

from shared import console_token


def test_roundtrip_returns_the_port():
    token = console_token.issue_token("s1", "cloudx-comp-01", 5901)

    assert console_token.verify_token(token, "cloudx-comp-01") == 5901


def test_expired_token_rejected():
    token = console_token.issue_token("s1", "cloudx-comp-01", 5901, ttl=-1)

    with pytest.raises(ValueError):
        console_token.verify_token(token, "cloudx-comp-01")


def test_wrong_host_rejected():
    token = console_token.issue_token("s1", "cloudx-comp-01", 5901)

    with pytest.raises(ValueError):
        console_token.verify_token(token, "cloudx-comp-02")


def test_wrong_type_rejected():
    # a normal auth-api session token, signed with the very same key
    token = jwt.encode({"sub": "alice", "exp": int(time.time()) + 300, "host": "cloudx-comp-01", "port": 5901},
                       os.environ["AUTH_SECRET_KEY"], algorithm="HS256")

    with pytest.raises(ValueError):
        console_token.verify_token(token, "cloudx-comp-01")


def test_tampered_signature_rejected():
    token = console_token.issue_token("s1", "cloudx-comp-01", 5901)
    forged = jwt.encode(jwt.get_unverified_claims(token), "some-other-key", algorithm="HS256")

    with pytest.raises(ValueError):
        console_token.verify_token(forged, "cloudx-comp-01")


@pytest.mark.parametrize("port", [None, "5901", "abc"])
def test_missing_or_non_int_port_rejected(port):
    claims = {"typ": "console", "sid": "s1", "host": "cloudx-comp-01", "exp": int(time.time()) + 300}
    if port is not None:
        claims["port"] = port
    token = jwt.encode(claims, os.environ["AUTH_SECRET_KEY"], algorithm="HS256")

    with pytest.raises(ValueError):
        console_token.verify_token(token, "cloudx-comp-01")


def test_garbage_rejected():
    with pytest.raises(ValueError):
        console_token.verify_token("not-a-jwt", "cloudx-comp-01")
