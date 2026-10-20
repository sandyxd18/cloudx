import importlib

import pytest
from fastapi.testclient import TestClient

from shared.auth_middleware import require_auth, require_internal


@pytest.fixture
def fs(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'fs.sqlite'}")
    monkeypatch.setenv("FILE_STORE_HOST", "node1")
    import services.file_store.app.main as m
    m = importlib.reload(m)
    m.Base.metadata.create_all(bind=m.engine)
    calls = []

    def fake_net(method, path, **kw):
        calls.append((method, path))
        if method == "POST" and path == "/ports":
            return {"id": "port1", "name": "fs-port1", "mac_address": "fa:16:3e:00:00:01", "network_id": "n1",
                    "fixed_ips": [{"subnet_id": "s1", "ip_address": "10.0.0.50"}]}
        if path.startswith("/subnets/"):
            return {"cidr": "10.0.0.0/24", "gateway_ip": "10.0.0.1"}
        if path.startswith("/networks/"):
            return {"name": "private-net"}
        return {}

    monkeypatch.setattr(m, "_net", fake_net)
    who = {"project_id": "p1", "role": "member"}
    m.app.dependency_overrides[require_auth] = lambda: who
    m.app.dependency_overrides[require_internal] = lambda: None
    c = TestClient(m.app)
    c.who, c.calls = who, calls
    return c


def mk_net(c):
    r = c.post("/share-networks", json={"name": "sn", "network_id": "n1"})
    assert r.status_code == 201, r.text
    return r.json()


def activate(c, nid):
    c.post("/internal/fs-agent/node1/report", json={"share_networks": {nid: "active"}})


def test_flow_create_access_extend_delete(fs):
    sn = mk_net(fs)
    assert sn["status"] == "creating" and sn["ip"] == "10.0.0.50"
    assert fs.post("/shares", json={"name": "s", "size_gb": 1, "share_network_id": sn["id"]}).status_code == 409
    activate(fs, sn["id"])
    sh = fs.post("/shares", json={"name": "s", "size_gb": 1, "share_network_id": sn["id"]})
    assert sh.status_code == 201 and sh.json()["export_location"] == f"10.0.0.50:/{sh.json()['id']}"
    sid = sh.json()["id"]
    assert fs.post("/shares", json={"name": "s", "size_gb": 1, "share_network_id": sn["id"]}).status_code == 409
    agent = fs.get("/internal/fs-agent/node1").json()
    assert agent["shares"][0]["status"] == "creating" and agent["shares"][0]["rules"] == []
    fs.post("/internal/fs-agent/node1/report", json={"shares": {sid: "available"}})
    assert fs.post(f"/shares/{sid}/extend", json={"size_gb": 1}).status_code == 409
    assert fs.post(f"/shares/{sid}/extend", json={"size_gb": 3}).json()["status"] == "extending"
    assert fs.delete(f"/share-networks/{sn['id']}").status_code == 409
    assert fs.delete(f"/shares/{sid}").status_code == 202
    fs.post("/internal/fs-agent/node1/report", json={"shares": {sid: "deleted"}})
    assert fs.get(f"/shares/{sid}").status_code == 404
    assert fs.delete(f"/share-networks/{sn['id']}").status_code == 202
    fs.post("/internal/fs-agent/node1/report", json={"share_networks": {sn["id"]: "deleted"}})
    assert fs.get(f"/share-networks/{sn['id']}").status_code == 404
    assert ("DELETE", "/ports/port1") in fs.calls


def test_access_rules(fs):
    sn = mk_net(fs)
    activate(fs, sn["id"])
    sid = fs.post("/shares", json={"name": "s", "size_gb": 1, "share_network_id": sn["id"]}).json()["id"]
    p = f"/shares/{sid}/access"
    for bad in ({"to": "0.0.0.0/0", "level": "rw"}, {"to": "10.0.0.0/7", "level": "rw"}, {"to": "x", "level": "rw"},
                {"to": "10.0.0.5", "level": "root"}):
        assert fs.post(p, json=bad).status_code == 422
    r = fs.post(p, json={"to": "10.0.0.5", "level": "ro"})
    assert r.status_code == 201 and r.json()["to"] == "10.0.0.5/32"
    assert fs.post(p, json={"to": "10.0.0.5/32", "level": "rw"}).status_code == 409
    assert fs.get("/internal/fs-agent/node1").json()["shares"][0]["rules"] == [{"to": "10.0.0.5/32", "level": "ro"}]
    assert fs.delete(f"{p}/{r.json()['id']}").status_code == 204
    assert fs.get(p).json()["items"] == []


def test_other_project_cannot_see(fs):
    sn = mk_net(fs)
    activate(fs, sn["id"])
    sid = fs.post("/shares", json={"name": "s", "size_gb": 1, "share_network_id": sn["id"]}).json()["id"]
    fs.who.update(project_id="p2")
    assert fs.get(f"/shares/{sid}").status_code == 404
    assert fs.get("/shares").json()["items"] == []
    assert fs.post(f"/shares/{sid}/access", json={"to": "10.0.0.5", "level": "rw"}).status_code == 404


def test_ready_at_is_set_once_and_stale_errors_list_only_never_ready(fs):
    from datetime import datetime, timedelta
    m = importlib.import_module("services.file_store.app.main")
    sn = mk_net(fs)
    activate(fs, sn["id"])
    sh = fs.post("/shares", json={"name": "ok", "size_gb": 1, "share_network_id": sn["id"]}).json()
    fs.post("/internal/fs-agent/node1/report", json={"shares": {sh["id"]: "available"}})
    bad = fs.post("/shares", json={"name": "bad", "size_gb": 1, "share_network_id": sn["id"]}).json()
    fs.post("/internal/fs-agent/node1/report", json={"shares": {bad["id"]: "error"}})
    old = datetime.utcnow() - timedelta(hours=30)
    with m.SessionLocal() as s:
        assert s.get(m.Share, sh["id"]).ready_at is not None
        assert s.get(m.ShareNetwork, sn["id"]).ready_at is not None
        for row in (s.get(m.Share, sh["id"]), s.get(m.Share, bad["id"])):
            row.created_at = old
        s.get(m.Share, sh["id"]).status = "error"   # was ready once, then broke: not reaped
        s.commit()
    items = fs.get("/internal/stale-errors?hours=24").json()["items"]
    assert items == [{"id": bad["id"], "kind": "share"}]
    assert fs.get("/internal/stale-errors?hours=48").json()["items"] == []
