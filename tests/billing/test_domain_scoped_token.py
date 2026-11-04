"""A domain-scoped token has project_id == "": billing must answer for the domain, never 500 or invent a project."""
import importlib
import sys
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from jose import jwt


@pytest.fixture
def billing(monkeypatch, tmp_path):
    monkeypatch.setenv("REVOCATION_CHECK", "false")
    monkeypatch.delenv("ACCEPT_DOMAIN_SCOPE", raising=False)
    monkeypatch.setenv("BILLING_DATABASE_URL", f"sqlite:///{tmp_path / 'billing.sqlite'}")
    sys.modules.pop("services.billing.app.main", None)
    b = importlib.import_module("services.billing.app.main")
    b.Base.metadata.create_all(b.engine)
    with b.engine.begin() as conn:  # stands in for cloudx-db-migrate
        conn.exec_driver_sql("CREATE TABLE alembic_version_billing (version_num VARCHAR(32))")
    monkeypatch.setattr("shared.domain.domain_projects", lambda d: ["p1", "p1b"] if d == "d1" else [])
    return b


def token(**over):
    claims = {"sub": "da1", "username": "da1", "project_id": "", "role": "member", "roles": ["member"],
              "scope_type": "domain", "domain_id": "d1", "domain_admin": True,
              "exp": datetime.now(timezone.utc) + timedelta(hours=1), **over}
    from shared import auth_middleware as am
    return {"X-Auth-Token": jwt.encode(claims, am.SECRET_KEY, algorithm=am.ALGORITHM)}


def test_billing_opts_in_and_serves_a_domain_token(billing):
    c = TestClient(billing.app)
    assert c.get("/billing/usage-records", headers=token()).status_code == 200
    assert c.get("/billing/invoices", headers=token()).status_code == 200
    r = c.get("/billing/summary", headers=token())
    assert r.status_code == 200 and r.json()["project_id"] == "domain:d1"
    r = c.post("/billing/invoices", headers=token())
    assert r.status_code == 201 and r.json()["project_id"] == "domain:d1"  # not "__none__"


def test_domain_token_cannot_reach_another_domain(billing):
    c = TestClient(billing.app)
    r = c.get("/billing/usage-records?domain_id=d2", headers=token())
    assert r.status_code == 200 and r.json() == []
