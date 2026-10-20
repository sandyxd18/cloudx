import importlib
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from shared.auth_middleware import require_auth


class FakeApi:
    def __init__(self):
        self.host = "h1"
        self.vols = ["h2"]
        self.project = "p1"

    def server(self, sid):
        return {"id": sid, "host": self.host, "project_id": self.project, "status": "ACTIVE"}

    def server_volume_hosts(self, sid):
        return self.vols

    def servers(self):
        return [{"id": "s1", "host": "h1", "libvirt_domain_name": "dom-s1", "status": "ACTIVE"},
                {"id": "s2", "host": "h1", "libvirt_domain_name": "dom-s2", "status": "ACTIVE"},
                {"id": "s3", "host": "h2", "libvirt_domain_name": "dom-s3", "status": "ACTIVE"}]


@pytest.fixture
def ha(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'ha.sqlite'}")
    monkeypatch.setenv("INTERNAL_TOKEN", "tok")
    import services.ha.app.main as m
    m = importlib.reload(m)
    m.Base.metadata.create_all(bind=m.engine)
    fake = FakeApi()
    monkeypatch.setattr(m, "make_api", lambda: fake)
    monkeypatch.setattr(m, "start_monitor", lambda: None)
    who = {"project_id": "p1", "role": "member"}
    m.app.dependency_overrides[require_auth] = lambda: who
    c = TestClient(m.app)
    c.who, c.fake, c.m = who, fake, m
    return c


HB = {"X-Internal-Token": "tok"}


def test_heartbeat_upserts_and_requires_token(ha):
    assert ha.post("/internal/heartbeat/h1", json={"initiator_iqn": "iqn.a"}).status_code in (401, 403)
    assert ha.post("/internal/heartbeat/h1", json={"initiator_iqn": "iqn.a"}, headers=HB).status_code == 200
    ha.post("/internal/heartbeat/h1", json={}, headers=HB)
    h = ha.get("/hosts").json()["items"][0]
    assert h["host"] == "h1" and h["state"] == "up" and h["has_iqn"]


def test_heartbeat_does_not_unfence(ha):
    ha.post("/internal/heartbeat/h1", json={}, headers=HB)
    with ha.m.SessionLocal() as db:
        db.get(ha.m.Host, "h1").state = "fenced"
        db.commit()
    ha.post("/internal/heartbeat/h1", json={}, headers=HB)
    assert ha.get("/hosts").json()["items"][0]["state"] == "fenced"


def test_protect_rejects_volume_on_own_host(ha):
    ha.fake.vols = ["h2", "h1"]
    assert ha.put("/protected/s1").status_code == 422
    ha.fake.vols = []
    assert ha.put("/protected/s1").status_code == 422
    ha.fake.vols = [None]
    assert ha.put("/protected/s1").status_code == 422


def test_protect_ok_and_tenant_isolation(ha):
    assert ha.put("/protected/s1").status_code == 200
    assert ha.put("/protected/s1").status_code == 200  # idempotent
    assert len(ha.get("/protected").json()["items"]) == 1
    ha.fake.project = "p2"
    assert ha.put("/protected/s2").status_code == 404
    ha.who["project_id"] = "p3"
    assert ha.get("/protected").json()["items"] == []
    assert ha.delete("/protected/s1").status_code == 404
    ha.who["project_id"] = "p1"
    assert ha.delete("/protected/s1").status_code == 204


def test_reset_admin_only(ha):
    ha.post("/internal/heartbeat/h1", json={}, headers=HB)
    with ha.m.SessionLocal() as db:
        h = db.get(ha.m.Host, "h1")
        h.state, h.fails = "fenced", 3
        db.commit()
    assert ha.post("/hosts/h1/reset").status_code == 403
    ha.who["role"] = "admin"
    assert ha.post("/hosts/h1/reset").json()["state"] == "up"
    assert ha.post("/hosts/nope/reset").status_code == 404


def test_startup_refreshes_last_seen(ha):
    old = datetime.utcnow() - timedelta(hours=1)
    with ha.m.SessionLocal() as db:
        db.add(ha.m.Host(host="h9", last_seen=old, state="up", fails=0))
        db.commit()
    ha.m.require_schema = lambda *a: None
    ha.m.startup()
    with ha.m.SessionLocal() as db:
        assert db.get(ha.m.Host, "h9").last_seen > old + timedelta(minutes=59)

def test_watchdog_endpoints(ha):
    for u in ("/internal/protected-domains/h1", "/internal/hosts/h1"):
        assert ha.get(u).status_code in (401, 403)
    assert ha.post("/internal/watchdog/h1").status_code in (401, 403)
    ha.put("/protected/s1")
    ha.fake.vols = ["h2"]
    assert ha.get("/internal/protected-domains/h1", headers=HB).json() == {"domains": ["dom-s1"]}
    assert ha.get("/internal/hosts/h1", headers=HB).status_code == 404
    ha.post("/internal/heartbeat/h1", json={}, headers=HB)
    assert ha.get("/internal/hosts/h1", headers=HB).json() == {"state": "up"}
    assert ha.post("/internal/watchdog/h1", headers=HB).status_code == 200
    with ha.m.SessionLocal() as db:
        assert db.get(ha.m.Host, "h1").watchdog_at is not None
