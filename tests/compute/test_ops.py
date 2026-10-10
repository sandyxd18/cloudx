import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from tests.stack import fresh_service


@pytest.fixture
def compute(monkeypatch, tmp_path: Path):
    svc = fresh_service("compute", monkeypatch, tmp_path)
    db = svc.SessionLocal()
    yield svc, db
    db.close()


def _agg(svc, db, name, az, hosts, meta=None):
    a = svc.Aggregate(id=name, name=name, availability_zone=az, extra_metadata=json.dumps(meta or {}))
    db.add(a)
    db.add_all(svc.AggregateHost(aggregate_id=name, host=h) for h in hosts)
    db.commit()


def test_filters(compute):
    svc, db = compute
    _agg(svc, db, "a", "az1", ["n1", "n2"], {"ssd": "true"})
    _agg(svc, db, "b", "az2", ["n3"])
    flavor = svc.Flavor(name="f", vcpus=1, ram_mb=1, disk_gb=1, extra_specs=json.dumps({"aggregate_instance_extra_specs:ssd": "true"}))
    assert svc._constraints(db, flavor, az="az1")[1] == ["n1", "n2"]
    assert svc._constraints(db, flavor)[1] == ["n1", "n2"]  # extra_specs alone pins to aggregate a
    db.add(svc.ComputeService(host="n1", disabled=True, forced_down=False))
    db.commit()
    assert svc._constraints(db, flavor, az="az1") == (["n1"], ["n2"])
    with pytest.raises(HTTPException) as e:
        svc._constraints(db, flavor, az="az2")  # n3 is not in the ssd aggregate
    assert e.value.status_code == 409
    with pytest.raises(HTTPException) as e:
        svc._constraints(db, flavor, az="nope")
    assert e.value.status_code == 400

def test_quota_set_maps_names_and_rejects_unknown(compute, monkeypatch):
    svc, _ = compute
    from fastapi.testclient import TestClient
    from shared.auth_middleware import require_auth
    ops = svc.ops
    seen = {}

    class R:
        status_code = 200
        text = ""
        def json(self): return {"project_id": "p1", "max_instances": 7, "max_vcpus": 9, "max_ram_mb": 1}

    def fake(method, url, **kw):
        seen.update(method=method, json=kw.get("json"))
        return R()

    monkeypatch.setattr(ops.requests, "request", fake)
    svc.app.dependency_overrides[require_auth] = lambda: {"role": "admin", "project_id": "p1", "sub": "u"}
    c = TestClient(svc.app)
    r = c.put("/os-quota-sets/p1", json={"quota_set": {"instances": 7, "cores": 9}})
    assert r.status_code == 200 and r.json()["quota_set"]["instances"] == 7
    assert seen["json"] == {"max_instances": 7, "max_vcpus": 9}
    assert c.put("/os-quota-sets/p1", json={"quota_set": {"bogus": 1}}).status_code == 400
    assert c.get("/os-quota-sets/p1").json()["quota_set"]["cores"] == 9
