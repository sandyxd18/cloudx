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


def test_relocate_preserves_shutoff_status_and_supports_data_volumes(compute, monkeypatch):
    for key in ("IMAGE_API_URL", "NETWORKING_API_URL", "BLOCK_STORE_API_URL"):
        monkeypatch.setenv(key, "http://peer.test:1")
    svc, db = compute
    ops = svc.ops

    server = svc.Server(
        id="srv-shutoff", name="shutoff-vm", project_id="p1", image_id="img1",
        root_volume_id="vol-root", flavor="tiny", network_id="net1", port_id="port1",
        host="node-1", status="SHUTOFF"
    )
    db.add(server)
    db.add(svc.ComputeService(host="node-1", disabled=False, forced_down=False))
    db.add(svc.ComputeService(host="node-2", disabled=False, forced_down=False))
    db.commit()

    def fake_block_get(path):
        if path == "/volumes":
            return {"items": [
                {"id": "vol-root", "attached_to_server_id": "srv-shutoff"},
                {"id": "vol-data", "attached_to_server_id": "srv-shutoff", "target_device": "/dev/sdb"}
            ]}
        return {"id": "vol-root"}

    monkeypatch.setattr(ops.clients, "block_get", fake_block_get)
    monkeypatch.setattr(ops.clients, "api_get", lambda path: {"id": "port1", "mac_address": "00:11:22:33:44:55", "ip_address": "10.0.0.5"})
    casts = []
    monkeypatch.setattr(ops.clients, "cast_to_worker", lambda method, args: casts.append((method, args)))

    res = ops._relocate("srv-shutoff", "node-2", {"sub": "u1", "role": "admin", "project_id": "p1"}, db, False)

    assert res["from"] == "node-1"
    assert res["to"] == "node-2"
    assert casts[0][0] == "relocate_out"
    build = casts[0][1]["_build"]
    assert build["_target_status"] == "SHUTOFF"
    assert build["_data_volumes"] == [{"id": "vol-data", "target_device": "sdb"}]


def test_instance_actions_outcome_recording(compute):
    svc, db = compute
    server = svc.Server(
        id="srv-act", name="act-vm", project_id="p1", image_id="img1",
        flavor="tiny", network_id="net1", port_id="port1", status="ACTIVE"
    )
    db.add(server)
    db.commit()

    cur = {"sub": "u1", "project_id": "p1"}
    svc.record_action(db, "srv-act", "reboot", cur)

    actions = db.scalars(svc.select(svc.ServerAction).where(svc.ServerAction.server_id == "srv-act")).all()
    assert len(actions) == 1
    assert actions[0].result is None
    assert actions[0].finish_time is None

    svc.finish_action(db, "srv-act", result="success", message="reboot complete")

    db.refresh(actions[0])
    assert actions[0].result == "success"
    assert actions[0].message == "reboot complete"
    assert actions[0].finish_time is not None

def test_log_action_accepts_the_internal_token():
    """k8s-engine-worker deletes VMs with only X-Internal-Token; the router-wide log_action dependency must not demand a JWT."""
    import inspect

    from services.compute.app.routers import ops
    from shared.auth_middleware import UserOrInternal

    assert inspect.signature(ops.log_action).parameters["current"].annotation == UserOrInternal
