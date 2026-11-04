"""Bulk usage ingestion and cleanup are for an admin or a service holding the internal token (billing-worker)."""
import importlib
import sys
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from jose import jwt

BATCH = [{"project_id": "victim-project", "resource_type": "instance", "quantity": "1",
          "period_start": "2026-01-01T00:00:00Z", "period_end": "2026-01-02T00:00:00Z"}]


@pytest.fixture
def billing(monkeypatch, tmp_path):
    monkeypatch.setenv("REVOCATION_CHECK", "false")
    monkeypatch.setenv("INTERNAL_TOKEN", "service-token")
    monkeypatch.setenv("BILLING_DATABASE_URL", f"sqlite:///{tmp_path / 'billing.sqlite'}")
    sys.modules.pop("services.billing.app.main", None)
    b = importlib.import_module("services.billing.app.main")
    b.Base.metadata.create_all(b.engine)
    with b.engine.begin() as conn:  # stands in for cloudx-db-migrate
        conn.exec_driver_sql("CREATE TABLE alembic_version_billing (version_num VARCHAR(32))")
    b.client = TestClient(b.app)
    return b


def token(role="member", domain_admin=False):
    claims = {"sub": "u", "username": "u", "project_id": "p1", "role": role, "roles": [role],
              "scope_type": "project", "domain_id": "d1", "domain_admin": domain_admin,
              "exp": datetime.now(timezone.utc) + timedelta(hours=1)}
    from shared import auth_middleware as am
    return {"X-Auth-Token": jwt.encode(claims, am.SECRET_KEY, algorithm=am.ALGORITHM)}


INTERNAL = {"X-Internal-Token": "service-token"}


def add_old_record(b):
    with b.SessionLocal() as db:
        db.add(b.UsageRecord(
            project_id="victim-project", resource_type="instance", quantity=1, unit="hour", rate_per_unit=1, total_cost=1,
            period_start=datetime(2020, 1, 1, tzinfo=timezone.utc), period_end=datetime(2020, 1, 2, tzinfo=timezone.utc)))
        db.commit()


def records(b):
    with b.SessionLocal() as db:
        return db.query(b.UsageRecord).count()


@pytest.mark.parametrize("who", [token(), token(domain_admin=True)], ids=["member", "domain admin"])
def test_a_member_cannot_insert_usage_records(billing, who):
    r = billing.client.post("/billing/usage-records/batch", json=BATCH, headers=who)

    assert r.status_code == 403, r.text
    assert records(billing) == 0


@pytest.mark.parametrize("who", [token(), token(domain_admin=True)], ids=["member", "domain admin"])
def test_a_member_cannot_delete_usage_records(billing, who):
    add_old_record(billing)

    r = billing.client.post("/billing/cleanup?days=1", headers=who)

    assert r.status_code == 403, r.text
    assert records(billing) == 1


@pytest.mark.parametrize("path", ["/billing/usage-records/batch", "/billing/cleanup"])
def test_no_token_is_401(billing, path):
    r = billing.client.post(path, json=BATCH if path.endswith("batch") else None)
    assert r.status_code == 401


@pytest.mark.parametrize("who", [token(role="admin"), INTERNAL], ids=["admin", "internal (billing-worker)"])
def test_an_admin_and_the_internal_token_may_ingest_and_clean_up(billing, who):
    r = billing.client.post("/billing/usage-records/batch", json=BATCH, headers=who)
    assert r.status_code == 200 and set(r.json()) == {"created", "skipped"}

    add_old_record(billing)
    r = billing.client.post("/billing/cleanup?days=1", headers=who)
    assert r.status_code == 200 and r.json() == {"deleted": 1}
