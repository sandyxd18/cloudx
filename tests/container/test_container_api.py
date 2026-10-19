import importlib

import pytest
from fastapi.testclient import TestClient

from shared.auth_middleware import require_auth, require_internal


@pytest.fixture
def ct(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'ct.sqlite'}")
    import services.container.app.main as m
    m = importlib.reload(m)
    m.Base.metadata.create_all(bind=m.engine)
    calls = []

    def fake_net(method, path, **kw):
        calls.append((method, path, kw.get("json")))
        if method == "POST" and path == "/ports":
            return {"id": "port1", "name": "ct-port1", "mac_address": "fa:16:3e:00:00:02", "network_id": "n1",
                    "fixed_ips": [{"subnet_id": "s1", "ip_address": "10.0.0.60"}]}
        if path.startswith("/subnets/"):
            return {"cidr": "10.0.0.0/24", "gateway_ip": "10.0.0.1", "dns_nameservers": ["172.20.0.118"]}
        return {}

    monkeypatch.setattr(m, "_net", fake_net)
    who = {"project_id": "p1", "role": "member"}
    m.app.dependency_overrides[require_auth] = lambda: who
    m.app.dependency_overrides[require_internal] = lambda: None
    c = TestClient(m.app)
    c.who, c.calls = who, calls
    return c


def beat(c, host="node1"):
    return c.get(f"/internal/ct-agent/{host}").json()


BODY = {"name": "web", "image": "nginx:1.27", "network_id": "n1", "cpu": 0.5, "memory_mb": 128}


def test_no_live_host(ct):
    assert ct.post("/containers", json=BODY).status_code == 503


def test_flow(ct):
    beat(ct)
    r = ct.post("/containers", json=BODY)
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["status"] == "creating" and c["ip"] == "10.0.0.60" and c["host"] == "node1"
    port_call = next(x for x in ct.calls if x[:2] == ("POST", "/ports"))
    assert port_call[2]["device_owner"] == "cloudx:container"
    assert ct.post("/containers", json=BODY).status_code == 409
    want = beat(ct)["containers"][0]
    assert want["want"] == "running" and want["dns"] == ["172.20.0.118"] and want["mac"] == "fa:16:3e:00:00:02"
    ct.post("/internal/ct-agent/node1/report", json={"containers": {c["id"]: {"state": "running", "logs": "hello"}}})
    assert ct.get(f"/containers/{c['id']}").json()["status"] == "running"
    assert ct.get(f"/containers/{c['id']}/logs").json()["logs"] == "hello"
    assert ct.post(f"/containers/{c['id']}/stop").json()["status"] == "stopped"
    assert beat(ct)["containers"][0]["want"] == "stopped"
    assert ct.post(f"/containers/{c['id']}/start").json()["status"] == "creating"
    assert ct.post(f"/containers/{c['id']}/restart").json()["status"] == "creating"
    assert beat(ct)["containers"][0]["want"] == "restart"
    ct.post("/internal/ct-agent/node1/report", json={"containers": {c["id"]: {"state": "running"}}})
    assert beat(ct)["containers"][0]["want"] == "running"
    assert ct.delete(f"/containers/{c['id']}").status_code == 202
    ct.post("/internal/ct-agent/node1/report", json={"containers": {c["id"]: {"state": "deleted"}}})
    assert ct.get(f"/containers/{c['id']}").status_code == 404
    assert ("DELETE", "/ports/port1", None) in ct.calls


def test_data_path(ct):
    beat(ct)
    assert ct.post("/containers", json={**BODY, "data_path": "/etc"}).status_code == 422
    assert ct.post("/containers", json={**BODY, "data_path": "/var/lib/data"}).status_code == 201
    assert beat(ct)["containers"][0]["data_path"] == "/var/lib/data"


def test_validation(ct):
    beat(ct)
    for bad in ({"image": "nginx; rm"}, {"image": "-v"}, {"env": {"1A": "x"}}, {"cpu": 9}, {"memory_mb": 1},
                {"command": "sleep 1"}):
        assert ct.post("/containers", json={**BODY, **bad}).status_code == 422, bad


def test_picks_least_loaded_host(ct):
    beat(ct, "node1")
    beat(ct, "node2")
    ct.post("/containers", json=BODY)
    r = ct.post("/containers", json={**BODY, "name": "web2"})
    assert {ct.get("/containers").json()["items"][i]["host"] for i in (0, 1)} == {"node1", "node2"}
    assert r.status_code == 201


def test_delete_from_error_and_isolation(ct):
    beat(ct)
    cid = ct.post("/containers", json=BODY).json()["id"]
    ct.post("/internal/ct-agent/node1/report", json={"containers": {cid: {"state": "error", "error": "pull failed"}}})
    assert ct.get(f"/containers/{cid}").json()["error"] == "pull failed"
    ct.who.update(project_id="p2")
    assert ct.get(f"/containers/{cid}").status_code == 404
    assert ct.get("/containers").json()["items"] == []
    ct.who.update(project_id="p1")
    assert ct.delete(f"/containers/{cid}").status_code == 202
