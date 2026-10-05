"""The auth dependency every service uses: token extraction, verification and the admin check."""
from datetime import datetime, timedelta, timezone

import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jose import jwt
from shared import auth_middleware
from shared.auth_middleware import AdminUser, CurrentUser, OptionalAuth

app = FastAPI()


@app.get("/me")
def me(current: CurrentUser):
    return current


@app.get("/admin")
def admin_only(current: AdminUser):
    return {"ok": True}


@app.get("/maybe")
def maybe(current: OptionalAuth):
    return {"user": current and current["username"]}


client = TestClient(app)


def token(role="member", secret=None, minutes=10, **extra):
    claims = {"sub": "u-1", "username": "alice", "project_id": "p-1", "role": role,
              "exp": datetime.now(timezone.utc) + timedelta(minutes=minutes), **extra}
    return jwt.encode(claims, secret or auth_middleware.SECRET_KEY, algorithm=auth_middleware.ALGORITHM)


def test_no_credentials_is_401():
    assert client.get("/me").status_code == 401


@pytest.mark.parametrize("headers", [
    lambda t: {"Authorization": f"Bearer {t}"},
    lambda t: {"X-Auth-Token": t},
])
def test_both_token_headers_are_accepted(headers):
    res = client.get("/me", headers=headers(token()))
    assert res.status_code == 200 and res.json()["username"] == "alice"


@pytest.mark.parametrize("bad", [token(secret="another-secret-key-another-secret-key"), token(minutes=-5), "not-a-jwt"])
def test_forged_expired_or_garbage_tokens_are_401(bad):
    assert client.get("/me", headers={"Authorization": f"Bearer {bad}"}).status_code == 401


def test_admin_routes_need_the_admin_role():
    assert client.get("/admin", headers={"X-Auth-Token": token("member")}).status_code == 403
    assert client.get("/admin", headers={"X-Auth-Token": token("admin")}).status_code == 200


def test_optional_auth_never_rejects():
    assert client.get("/maybe").json() == {"user": None}
    assert client.get("/maybe", headers={"X-Auth-Token": "garbage"}).json() == {"user": None}
    assert client.get("/maybe", headers={"X-Auth-Token": token()}).json() == {"user": "alice"}


def test_access_key_headers_are_exchanged_for_a_token_at_auth_api(monkeypatch):
    seen = {}

    class Exchange:
        status_code = 201

        def json(self):
            return {"token": token()}

    def fake_post(url, headers=None, timeout=None):
        seen.update(url=url, headers=headers)
        return Exchange()

    monkeypatch.setattr(auth_middleware.requests, "post", fake_post)
    res = client.get("/me", headers={"X-Access-Key-Id": "CLDXAAAA", "X-Secret-Access-Key": "shh"})
    assert res.status_code == 200
    assert seen["url"].endswith("/auth/tokens/access-key") and seen["headers"]["X-Access-Key-Id"] == "CLDXAAAA"


def test_a_failed_access_key_exchange_is_401_not_500(monkeypatch):
    def down(*a, **k):
        raise requests.ConnectionError("auth-api down")

    monkeypatch.setattr(auth_middleware.requests, "post", down)
    res = client.get("/me", headers={"X-Access-Key-Id": "CLDXAAAA", "X-Secret-Access-Key": "shh"})
    assert res.status_code == 401
