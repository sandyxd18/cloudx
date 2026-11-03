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


def _raw_key():
    return os.environ["AUTH_SECRET_KEY"]


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
    token = jwt.encode(claims, console_token._secret(), algorithm="HS256")

    with pytest.raises(ValueError):
        console_token.verify_token(token, "cloudx-comp-01")


def test_garbage_rejected():
    with pytest.raises(ValueError):
        console_token.verify_token("not-a-jwt", "cloudx-comp-01")


@pytest.fixture(params=["empty", "missing"])
def no_secret(request, monkeypatch):
    if request.param == "empty":
        monkeypatch.setenv("AUTH_SECRET_KEY", "")
    else:
        monkeypatch.delenv("AUTH_SECRET_KEY")


def test_issue_without_a_secret_refused(no_secret):
    with pytest.raises(ValueError):
        console_token.issue_token("s1", "cloudx-comp-01", 5901)


def test_verify_without_a_secret_raises_value_error(no_secret):
    forged = jwt.encode({"typ": "console", "sid": "s1", "host": "cloudx-comp-01", "port": 1,
                         "exp": int(time.time()) + 300}, "", algorithm="HS256")

    with pytest.raises(ValueError):
        console_token.verify_token(forged, "cloudx-comp-01")


def test_a_token_signed_with_the_raw_secret_is_rejected_even_with_typ_console():
    forged = jwt.encode({"typ": "console", "sid": "s1", "host": "cloudx-comp-01", "port": 5901,
                         "exp": int(time.time()) + 300}, _raw_key(), algorithm="HS256")

    with pytest.raises(ValueError):
        console_token.verify_token(forged, "cloudx-comp-01")


def test_a_login_token_is_rejected_by_verify_token():
    from shared import auth_middleware

    login = jwt.encode({"sub": "u-1", "username": "alice", "project_id": "p-1", "role": "admin", "typ": "console",
                        "host": "cloudx-comp-01", "port": 5901, "exp": int(time.time()) + 300},
                       auth_middleware.SECRET_KEY, algorithm=auth_middleware.ALGORITHM)

    with pytest.raises(ValueError):
        console_token.verify_token(login, "cloudx-comp-01")


def test_a_console_token_is_not_a_login_token_for_the_shared_middleware():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from shared.auth_middleware import CurrentUser

    app = FastAPI()

    @app.get("/me")
    def me(current: CurrentUser):
        return current

    token = console_token.issue_token("s1", "cloudx-comp-01", 5901)

    res = TestClient(app).get("/me", headers={"X-Auth-Token": token})

    assert res.status_code == 401


def test_a_console_token_is_not_a_login_token_for_auth_api(monkeypatch, tmp_path):
    from fastapi import HTTPException
    from tests.stack import fresh_service

    auth = fresh_service("auth", monkeypatch, tmp_path)
    token = console_token.issue_token("s1", "cloudx-comp-01", 5901)

    with pytest.raises(HTTPException) as exc:
        auth.tokens._decode_token(token)
    assert exc.value.status_code == 401


def test_verify_claims_returns_the_port_and_the_domain():
    token = console_token.issue_token("s1", "cloudx-comp-01", 5901, domain="instance-0001")

    claims = console_token.verify_claims(token, "cloudx-comp-01")

    assert (claims["port"], claims["dom"], claims["sid"]) == (5901, "instance-0001", "s1")


def test_a_token_without_a_domain_has_no_dom_claim():
    token = console_token.issue_token("s1", "cloudx-comp-01", 5901)

    assert "dom" not in console_token.verify_claims(token, "cloudx-comp-01")
    assert console_token.verify_token(token, "cloudx-comp-01") == 5901
