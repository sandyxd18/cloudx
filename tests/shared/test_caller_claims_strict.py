"""X-Caller-Claims on UserOrInternal / AdminOrInternal: the identity a service holding INTERNAL_TOKEN states is the one used."""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "services"))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from jose import jwt  # noqa: E402

from shared import auth_middleware as am  # noqa: E402
from shared.auth_middleware import AdminOrInternal, InternalAuth, UserOrInternal  # noqa: E402

HEADER = "X-Caller-Claims"
MEMBER = {"sub": "u1", "username": "alice", "project_id": "p1", "role": "member", "roles": ["member"],
          "scope_type": "project"}
ADMIN = {"sub": "u9", "username": "root", "project_id": "p9", "role": "admin", "roles": ["admin"],
         "scope_type": "project"}
DOMAIN_ADMIN = {**MEMBER, "domain_admin": True, "domain_id": "d1"}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("REVOCATION_CHECK", "false")
    monkeypatch.setenv("INTERNAL_TOKEN", "tok")


@pytest.fixture
def client():
    app = FastAPI()

    @app.get("/user")
    def user(current: UserOrInternal):
        return {"who": current}

    @app.get("/admin")
    def admin(current: AdminOrInternal):
        return {"who": current}

    @app.get("/internal", dependencies=[InternalAuth])
    def internal():
        return {"ok": True}

    return TestClient(app)


def user_jwt():
    claims = {"sub": "jwt-user", "username": "j", "project_id": "pj", "role": "member", "roles": ["member"],
              "scope_type": "project", "exp": datetime.now(timezone.utc) + timedelta(hours=1)}
    return jwt.encode(claims, am.SECRET_KEY, algorithm=am.ALGORITHM)


def stated(claims, token="tok"):
    return {"X-Internal-Token": token, HEADER: claims if isinstance(claims, str) else json.dumps(claims)}


# ── a valid internal token ───────────────────────────────────────────────────

def test_valid_claims_are_the_identity_on_the_user_route(client):
    r = client.get("/user", headers=stated(MEMBER))
    assert r.status_code == 200 and r.json()["who"] == MEMBER


def test_valid_claims_are_the_identity_on_the_domain_admin_case(client):
    r = client.get("/user", headers=stated(DOMAIN_ADMIN))
    assert r.status_code == 200 and r.json()["who"] == DOMAIN_ADMIN


def test_member_claims_on_the_admin_route_are_refused(client):
    assert client.get("/admin", headers=stated(MEMBER)).status_code == 403


def test_admin_claims_on_the_admin_route_are_accepted(client):
    r = client.get("/admin", headers=stated(ADMIN))
    assert r.status_code == 200 and r.json()["who"] == ADMIN


@pytest.mark.parametrize("path", ["/user", "/admin"])
def test_no_header_is_the_internal_caller(client, path):
    r = client.get(path, headers={"X-Internal-Token": "tok"})
    assert r.status_code == 200
    assert r.json()["who"]["sub"] == "internal" and r.json()["who"]["role"] == "admin"


def test_the_claims_win_over_a_jwt_sent_alongside(client):
    r = client.get("/user", headers={**stated(MEMBER), "X-Auth-Token": user_jwt()})
    assert r.json()["who"] == MEMBER


BIG = {"role": "member", "roles": [f"r{i:04d}" for i in range(400)]}
INVALID = {
    "not json": "{nope",
    "empty": "",
    "list": "[]",
    "null": "null",
    "oversized": json.dumps(BIG, separators=(",", ":")),
    "role missing": json.dumps({"project_id": "p1"}),
    "role empty": json.dumps({"role": ""}),
    "role not a string": json.dumps({"role": ["admin"]}),
    "project_id not a string": json.dumps({"role": "member", "project_id": 5}),
    "domain_admin not a bool": json.dumps({"role": "member", "domain_admin": "true"}),
    "domain_admin without domain_id": json.dumps({"role": "member", "domain_admin": True}),
    "roles not a list": json.dumps({"role": "member", "roles": "admin"}),
}


@pytest.mark.parametrize("path", ["/user", "/admin"])
@pytest.mark.parametrize("value", INVALID.values(), ids=INVALID.keys())
def test_an_invalid_header_is_401(client, path, value):
    r = client.get(path, headers=stated(value))
    assert r.status_code == 401
    assert r.json()["detail"] == "invalid caller identity"
    assert "who" not in r.json()


@pytest.mark.parametrize("path", ["/user", "/admin"])
def test_an_invalid_header_is_401_even_with_a_jwt(client, path):
    r = client.get(path, headers={**stated("{nope"), "X-Auth-Token": user_jwt()})
    assert r.status_code == 401


@pytest.mark.parametrize("path", ["/user", "/admin"])
def test_two_headers_are_401(client, path):
    headers = [("X-Internal-Token", "tok"), (HEADER, json.dumps(MEMBER)), (HEADER, json.dumps(ADMIN))]
    assert client.get(path, headers=headers).status_code == 401


# ── no valid internal token: the header is ignored ──────────────────────────

@pytest.mark.parametrize("token_headers", [{}, {"X-Internal-Token": "wrong"}, {"X-Internal-Token": ""}],
                         ids=["none", "wrong", "empty"])
@pytest.mark.parametrize("path", ["/user", "/admin"])
def test_without_a_valid_token_the_header_is_ignored(client, path, token_headers):
    headers = {**token_headers, HEADER: json.dumps(ADMIN)}
    assert client.get(path, headers=headers).status_code == 401
    r = client.get("/user", headers={**headers, "X-Auth-Token": user_jwt()})
    assert r.status_code == 200
    assert r.json()["who"]["sub"] == "jwt-user" and r.json()["who"]["project_id"] == "pj"


def test_a_jwt_member_with_admin_claims_is_still_a_member(client):
    headers = {"X-Internal-Token": "wrong", HEADER: json.dumps(ADMIN), "X-Auth-Token": user_jwt()}
    assert client.get("/admin", headers=headers).status_code == 403


def test_an_invalid_header_without_a_valid_token_is_ignored(client):
    r = client.get("/user", headers={"X-Internal-Token": "wrong", HEADER: "{nope", "X-Auth-Token": user_jwt()})
    assert r.status_code == 200 and r.json()["who"]["sub"] == "jwt-user"


@pytest.mark.parametrize("path", ["/user", "/admin"])
def test_with_internal_token_unset_the_header_is_ignored(client, monkeypatch, path):
    monkeypatch.delenv("INTERNAL_TOKEN")
    for token in ("tok", ""):
        assert client.get(path, headers=stated(ADMIN, token=token)).status_code == 401
    r = client.get("/user", headers={**stated(ADMIN), "X-Auth-Token": user_jwt()})
    assert r.json()["who"]["sub"] == "jwt-user"


def test_with_internal_token_set_to_empty_the_header_is_ignored(client, monkeypatch):
    monkeypatch.setenv("INTERNAL_TOKEN", "")
    assert client.get("/user", headers=stated(ADMIN, token="")).status_code == 401


# ── other dependencies ───────────────────────────────────────────────────────

def test_internal_routes_do_not_read_the_header(client):
    for value in (json.dumps(MEMBER), "{nope"):
        assert client.get("/internal", headers=stated(value)).status_code == 200
    assert client.get("/internal", headers={HEADER: json.dumps(ADMIN)}).status_code == 401


def test_the_optional_dependency_still_answers_none_for_an_invalid_header():
    from starlette.requests import Request

    raw = [(b"x-internal-token", b"tok"), (b"x-caller-claims", b"{nope")]
    scope = {"type": "http", "method": "GET", "path": "/", "headers": raw}
    assert am.optional_user_or_internal(Request(scope)) is None
    two = [(b"x-internal-token", b"tok")] + [(b"x-caller-claims", b'{"role":"member"}')] * 2
    assert am.optional_user_or_internal(Request({**scope, "headers": two})) is None
