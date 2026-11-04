"""Bulk metric ingestion and cleanup are for an admin or a service holding the internal token (telemetry-worker)."""
import importlib
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from jose import jwt

BATCH = [{"instance_id": "i1", "project_id": "victim-project", "metric_type": "cpu_percent", "value": 1.0}]


@pytest.fixture
def telemetry(monkeypatch, tmp_path):
    monkeypatch.setenv("REVOCATION_CHECK", "false")
    monkeypatch.setenv("INTERNAL_TOKEN", "service-token")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    import services.telemetry.app.main as m

    m = importlib.reload(m)
    m.Base.metadata.create_all(m.engine)
    m.client = TestClient(m.app)
    return m


def token(role="member", domain_admin=False):
    claims = {"sub": "u", "username": "u", "project_id": "p1", "role": role, "roles": [role],
              "scope_type": "project", "domain_id": "d1", "domain_admin": domain_admin,
              "exp": datetime.now(timezone.utc) + timedelta(hours=1)}
    from shared import auth_middleware as am
    return {"X-Auth-Token": jwt.encode(claims, am.SECRET_KEY, algorithm=am.ALGORITHM)}


INTERNAL = {"X-Internal-Token": "service-token"}


def add_old_metric(m):
    old = datetime.now(timezone.utc) - timedelta(days=30)
    with m.SessionLocal() as db:
        db.add(m.TelemetryMetric(instance_id="i1", project_id="victim-project", metric_type="cpu_percent", value=1.0, timestamp=old))
        db.commit()


def metrics(m):
    with m.SessionLocal() as db:
        return db.query(m.TelemetryMetric).count()


@pytest.mark.parametrize("who", [token(), token(domain_admin=True)], ids=["member", "domain admin"])
def test_a_member_cannot_insert_metrics(telemetry, who):
    r = telemetry.client.post("/telemetry/metrics/batch", json=BATCH, headers=who)

    assert r.status_code == 403, r.text
    assert metrics(telemetry) == 0


@pytest.mark.parametrize("who", [token(), token(domain_admin=True)], ids=["member", "domain admin"])
def test_a_member_cannot_run_the_cleanup(telemetry, who):
    add_old_metric(telemetry)

    r = telemetry.client.delete("/telemetry/metrics/cleanup", headers=who)

    assert r.status_code == 403, r.text
    assert metrics(telemetry) == 1


@pytest.mark.parametrize("method, path", [("post", "/telemetry/metrics/batch"), ("delete", "/telemetry/metrics/cleanup")])
def test_no_token_is_401(telemetry, method, path):
    r = telemetry.client.request(method, path, **({"json": BATCH} if method == "post" else {}))
    assert r.status_code == 401


@pytest.mark.parametrize("who", [token(role="admin"), INTERNAL], ids=["admin", "internal (telemetry-worker)"])
def test_an_admin_and_the_internal_token_may_ingest_and_clean_up(telemetry, who):
    r = telemetry.client.post("/telemetry/metrics/batch", json=BATCH, headers=who)
    assert r.status_code == 201 and r.json() == {"ingested": 1}

    assert telemetry.client.delete("/telemetry/metrics/cleanup", headers=who).status_code == 204
