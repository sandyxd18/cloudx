"""require_auth refuses domain-scoped tokens unless the service opts in with ACCEPT_DOMAIN_SCOPE."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jose import jwt
from shared import auth_middleware
from shared.auth_middleware import CurrentUser, UserOrInternal

app = FastAPI()


@app.get("/servers")
def list_servers(current: CurrentUser):
    return {"project": current["project_id"]}  # what a project-only service does


@app.get("/either")
def either(current: UserOrInternal):
    return {"sub": current["sub"]}


client = TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _no_auth_api(monkeypatch):
    monkeypatch.setenv("REVOCATION_CHECK", "false")
    monkeypatch.delenv("ACCEPT_DOMAIN_SCOPE", raising=False)


def token(scope_type="project", project_id="p1", role="member"):
    claims = {"sub": "u", "username": "u", "project_id": project_id, "role": role, "roles": [role],
              "scope_type": scope_type, "domain_id": "d1", "domain_admin": scope_type == "domain",
              "exp": datetime.now(timezone.utc) + timedelta(hours=1)}
    return jwt.encode(claims, auth_middleware.SECRET_KEY, algorithm=auth_middleware.ALGORITHM)


def get(path, tok, **headers):
    return client.get(path, headers={"X-Auth-Token": tok, **headers})


def test_domain_token_is_403_in_a_project_only_service():
    r = get("/servers", token("domain", project_id=""))
    assert r.status_code == 403 and r.json()["detail"] == "project-scoped token required"
    assert get("/either", token("domain", project_id="")).status_code == 403


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes"])
def test_domain_token_passes_when_service_opts_in(monkeypatch, value):
    monkeypatch.setenv("ACCEPT_DOMAIN_SCOPE", value)  # read at call time
    r = get("/servers", token("domain", project_id=""))
    assert r.status_code == 200 and r.json() == {"project": ""}


def test_opt_in_values_that_do_not_count(monkeypatch):
    for value in ("", "0", "false", "no"):
        monkeypatch.setenv("ACCEPT_DOMAIN_SCOPE", value)
        assert get("/servers", token("domain", project_id="")).status_code == 403


def test_project_and_system_tokens_unaffected():
    assert get("/servers", token("project")).json() == {"project": "p1"}
    assert get("/servers", token("system", role="admin")).json() == {"project": "p1"}


def test_internal_token_still_bypasses(monkeypatch):
    monkeypatch.setenv("INTERNAL_TOKEN", "secret123")
    r = client.get("/either", headers={"X-Internal-Token": "secret123"})
    assert r.status_code == 200 and r.json() == {"sub": "internal"}
