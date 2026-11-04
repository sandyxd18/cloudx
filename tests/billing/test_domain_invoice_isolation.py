"""A domain's consolidated invoice ("domain:<id>") is visible to, and creatable by, that domain's admin and the system admin only."""
import importlib
import sys
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from jose import jwt


@pytest.fixture
def billing(monkeypatch, tmp_path):
    monkeypatch.setenv("REVOCATION_CHECK", "false")
    monkeypatch.setenv("BILLING_DATABASE_URL", f"sqlite:///{tmp_path / 'billing.sqlite'}")
    sys.modules.pop("services.billing.app.main", None)
    b = importlib.import_module("services.billing.app.main")
    b.Base.metadata.create_all(b.engine)
    with b.engine.begin() as conn:  # stands in for cloudx-db-migrate
        conn.exec_driver_sql("CREATE TABLE alembic_version_billing (version_num VARCHAR(32))")
    monkeypatch.setattr("shared.domain.domain_projects", lambda d: {"d1": ["p1", "p1b"], "d2": ["p2"]}.get(d, []))
    now = datetime.now(timezone.utc)
    with b.SessionLocal() as db:
        for n, pid in enumerate(["domain:d1", "domain:d2", "p1", "p2"], start=1):
            db.add(b.Invoice(invoice_number=f"INV-{n}", project_id=pid, period_start=now - timedelta(days=30), period_end=now))
        db.commit()
    return b


def token(role="member", project_id="p1", domain_id="d1", domain_admin=False):
    claims = {"sub": "u", "username": "u", "project_id": project_id, "role": role, "roles": [role],
              "scope_type": "project", "domain_id": domain_id, "domain_admin": domain_admin,
              "exp": datetime.now(timezone.utc) + timedelta(hours=1)}
    from shared import auth_middleware as am
    return {"X-Auth-Token": jwt.encode(claims, am.SECRET_KEY, algorithm=am.ALGORITHM)}


def listed(billing, headers, query=""):
    r = TestClient(billing.app).get(f"/billing/invoices{query}", headers=headers)
    assert r.status_code == 200, r.text
    return sorted(i["project_id"] for i in r.json())


# ── listing ──────────────────────────────────────────────────────────────────

def test_a_plain_member_does_not_see_another_domains_consolidated_invoice(billing):
    assert listed(billing, token(), "?domain_id=d2") == []


def test_a_plain_member_does_not_see_even_their_own_domains_consolidated_invoice(billing):
    assert listed(billing, token(), "?domain_id=d1") == []


def test_a_member_still_sees_their_own_projects_invoices(billing):
    assert listed(billing, token()) == ["p1"]


def test_a_domain_admin_does_not_see_another_domains_consolidated_invoice(billing):
    assert listed(billing, token(domain_admin=True), "?domain_id=d2") == []


def test_a_domain_admin_sees_their_own_domains_invoices(billing):
    assert listed(billing, token(domain_admin=True), "?domain_id=d1") == ["domain:d1", "p1"]


def test_the_system_admin_sees_any_domains_invoices(billing):
    admin = token(role="admin", project_id="admin", domain_id="default")
    assert listed(billing, admin, "?domain_id=d2") == ["domain:d2", "p2"]
    assert listed(billing, admin, "?domain_id=d1") == ["domain:d1", "p1"]


# ── creating ─────────────────────────────────────────────────────────────────

def test_a_domain_admin_cannot_create_an_invoice_for_another_domain(billing):
    r = TestClient(billing.app).post("/billing/invoices?domain_id=d3", headers=token(domain_admin=True))

    assert r.status_code == 403, r.text
    with billing.SessionLocal() as db:
        assert db.query(billing.Invoice).filter(billing.Invoice.project_id == "domain:d3").count() == 0


def test_a_domain_admin_can_create_their_own_domains_invoice(billing):
    r = TestClient(billing.app).post("/billing/invoices?domain_id=d1&year=2026&month=10", headers=token(domain_admin=True))

    assert r.status_code == 201, r.text
    assert r.json()["project_id"] == "domain:d1"


def test_the_system_admin_can_create_any_domains_invoice(billing):
    admin = token(role="admin", project_id="admin", domain_id="default")

    r = TestClient(billing.app).post("/billing/invoices?domain_id=d2&year=2026&month=10", headers=admin)

    assert r.status_code == 201, r.text
    assert r.json()["project_id"] == "domain:d2"
