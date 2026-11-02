"""rebuild, rescue and unrescue rebuild the domain, so the worker needs every port of the server (MAC and logical port name
for the NICs, address for the config drive). The payload only had the port ids, and the worker made one NIC: a server on
two networks came out with one."""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from shared.auth_middleware import require_auth
from tests.stack import fresh_service

ADMIN = {"role": "admin", "project_id": "p1", "sub": "u"}
PORTS = {
    "port1": {"id": "port1", "name": "lp-main", "mac_address": "02:00:00:00:00:01", "fixed_ip": "10.10.0.5"},
    "port2": {"id": "port2", "name": "lp-extra", "mac_address": "02:00:00:00:00:02", "fixed_ip": "10.20.0.5"},
}
ACTIONS = [
    ("rebuild", {"image": "img2"}, "rebuild"),
    ("rescue", {}, "rescue"),
    ("unrescue", None, "unrescue"),
]


@pytest.fixture
def api(monkeypatch, tmp_path):
    svc = fresh_service("compute", monkeypatch, tmp_path)
    monkeypatch.setenv("IMAGE_API_URL", "http://image.test")
    monkeypatch.setenv("NETWORKING_API_URL", "http://networking.test")
    svc.app.dependency_overrides[require_auth] = lambda: ADMIN
    casts = []
    monkeypatch.setattr(svc.clients, "cast_to_worker", lambda method, payload: casts.append((method, payload)))

    def api_get(url):
        if "/ports/" in url:
            return PORTS[url.rsplit("/", 1)[1]]
        return {"id": "img2", "name": "img2"}

    monkeypatch.setattr(svc.clients, "api_get", api_get)
    db = svc.SessionLocal()
    db.add(svc.Flavor(name="small", vcpus=1, ram_mb=512, disk_gb=1))
    db.commit()
    yield svc, TestClient(svc.app, raise_server_exceptions=False), casts
    db.close()


def _server(svc, **extra):
    db = svc.SessionLocal()
    db.add(svc.Server(id="srv1", name="vm1", project_id="p1", image_id="img1", root_volume_id="vol1", flavor="small",
                      network_id="net1", port_id="port1", host="node-1", status="ACTIVE", **extra))
    db.commit()
    db.close()


def _status(svc):
    db = svc.SessionLocal()
    try:
        return db.get(svc.Server, "srv1").status
    finally:
        db.close()


@pytest.mark.parametrize("action,body,method", ACTIONS)
def test_every_port_of_a_server_on_two_networks_goes_to_the_worker(api, action, body, method):
    svc, client, casts = api
    _server(svc, extra_port_ids="port2")

    res = client.post(f"/servers/srv1/{action}", json=body) if body is not None else client.post(f"/servers/srv1/{action}")

    assert res.status_code < 300, res.text
    (cast_method, payload), = casts
    assert cast_method == method
    assert payload["port"]["id"] == "port1"
    assert [p["id"] for p in payload["ports"]] == ["port1", "port2"], "primary first, then the extra ports in order"
    assert payload["ports"][1]["mac_address"] == "02:00:00:00:00:02"


@pytest.mark.parametrize("action,body,method", ACTIONS)
def test_a_server_on_one_network_sends_its_only_port(api, action, body, method):
    svc, client, casts = api
    _server(svc)

    res = client.post(f"/servers/srv1/{action}", json=body) if body is not None else client.post(f"/servers/srv1/{action}")

    assert res.status_code < 300, res.text
    (_, payload), = casts
    assert payload["port"]["id"] == "port1"
    assert [p["id"] for p in payload["ports"]] == ["port1"]


@pytest.mark.parametrize("action,body,method", ACTIONS)
def test_an_unreachable_networking_api_is_502_and_changes_nothing(api, monkeypatch, action, body, method):
    svc, client, casts = api
    _server(svc, extra_port_ids="port2")

    def down(url):
        if "/ports/" in url:
            raise HTTPException(status_code=502, detail="networking-api is unreachable")
        return {"id": "img2", "name": "img2"}

    monkeypatch.setattr(svc.clients, "api_get", down)

    res = client.post(f"/servers/srv1/{action}", json=body) if body is not None else client.post(f"/servers/srv1/{action}")

    assert res.status_code == 502
    assert casts == []
    assert _status(svc) == "ACTIVE", "the status is only changed once the ports are in hand"
