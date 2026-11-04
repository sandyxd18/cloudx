"""OptionalUserOrInternal: the caller's identity (internal token or user JWT), or None. Never a 401."""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "services"))

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from jose import jwt  # noqa: E402

from shared import auth_middleware as am  # noqa: E402
from shared.auth_middleware import OptionalUserOrInternal, require_auth  # noqa: E402


@pytest.fixture(autouse=True)
def _no_revocation_lookup(monkeypatch):
    monkeypatch.setenv("REVOCATION_CHECK", "false")


def make_app():
    app = FastAPI()

    @app.get("/who")
    def who(current: OptionalUserOrInternal):
        return {"who": current}

    return app


def user_token(exp_delta=timedelta(hours=1)):
    claims = {"sub": "u", "username": "u", "project_id": "p1", "role": "member", "roles": ["member"],
              "scope_type": "project", "exp": datetime.now(timezone.utc) + exp_delta}
    return jwt.encode(claims, am.SECRET_KEY, algorithm=am.ALGORITHM)


def test_internal_token_gives_the_synthetic_admin(monkeypatch):
    monkeypatch.setenv("INTERNAL_TOKEN", "tok")
    who = TestClient(make_app()).get("/who", headers={"X-Internal-Token": "tok"}).json()["who"]
    assert who["sub"] == "internal"
    assert who["role"] == "admin"


def test_a_wrong_internal_token_is_anonymous(monkeypatch):
    monkeypatch.setenv("INTERNAL_TOKEN", "tok")
    r = TestClient(make_app()).get("/who", headers={"X-Internal-Token": "wrong"})
    assert r.status_code == 200
    assert r.json() == {"who": None}


def test_a_valid_user_token_gives_its_claims(monkeypatch):
    monkeypatch.delenv("INTERNAL_TOKEN", raising=False)
    r = TestClient(make_app()).get("/who", headers={"X-Auth-Token": user_token()})
    assert r.status_code == 200
    assert r.json()["who"]["sub"] == "u"
    assert r.json()["who"]["project_id"] == "p1"


def test_no_token_is_anonymous(monkeypatch):
    monkeypatch.setenv("INTERNAL_TOKEN", "tok")
    r = TestClient(make_app()).get("/who")
    assert r.status_code == 200
    assert r.json() == {"who": None}


def test_a_garbage_or_expired_token_is_anonymous_not_401(monkeypatch):
    monkeypatch.delenv("INTERNAL_TOKEN", raising=False)
    c = TestClient(make_app())
    for headers in ({"X-Auth-Token": "not-a-jwt"}, {"X-Auth-Token": user_token(timedelta(hours=-1))}):
        r = c.get("/who", headers=headers)
        assert r.status_code == 200
        assert r.json() == {"who": None}


def test_the_require_auth_override_is_honoured(monkeypatch):
    monkeypatch.delenv("INTERNAL_TOKEN", raising=False)
    app = make_app()
    app.dependency_overrides[require_auth] = lambda: {"sub": "fake", "role": "member"}
    assert TestClient(app).get("/who").json() == {"who": {"sub": "fake", "role": "member"}}

    def deny():
        raise HTTPException(status_code=401, detail="no")

    app.dependency_overrides[require_auth] = deny
    r = TestClient(app).get("/who")
    assert r.status_code == 200
    assert r.json() == {"who": None}


def test_without_internal_token_configured_a_header_does_not_open_anything(monkeypatch):
    monkeypatch.delenv("INTERNAL_TOKEN", raising=False)
    for value in ("anything", ""):
        r = TestClient(make_app()).get("/who", headers={"X-Internal-Token": value})
        assert r.status_code == 200
        assert r.json() == {"who": None}


def test_a_revoked_token_is_anonymous_not_401(monkeypatch):
    monkeypatch.delenv("INTERNAL_TOKEN", raising=False)
    monkeypatch.setenv("REVOCATION_CHECK", "true")
    am._ok_jti.clear()
    claims = {"sub": "u", "username": "u", "project_id": "p1", "role": "member", "roles": ["member"],
              "scope_type": "project", "jti": "revoked-jti", "exp": datetime.now(timezone.utc) + timedelta(hours=1)}
    token = jwt.encode(claims, am.SECRET_KEY, algorithm=am.ALGORITHM)
    asked = []

    def revoked(url, **kwargs):
        asked.append(url)
        return type("R", (), {"status_code": 401})()

    monkeypatch.setattr(am.requests, "get", revoked)

    r = TestClient(make_app()).get("/who", headers={"X-Auth-Token": token})

    assert asked, "the revocation lookup was not made"
    assert r.status_code == 200
    assert r.json() == {"who": None}
    am._ok_jti.clear()
