import importlib

import pytest
from fastapi.testclient import TestClient
from shared import catalog
from shared.auth_middleware import require_auth
from tests.stack import fresh_service

PEERS = ("IMAGE_API_URL", "NETWORKING_API_URL", "BLOCK_STORE_API_URL", "PLACEMENT_API_URL")
ADMIN = {"role": "admin", "project_id": "p1", "sub": "u"}


def _down(service, interface="internal"):
    raise catalog.CatalogError(f"cannot resolve service '{service}': auth is unreachable")


@pytest.fixture
def api(monkeypatch, tmp_path):
    svc = fresh_service("compute", monkeypatch, tmp_path)
    svc.app.dependency_overrides[require_auth] = lambda: ADMIN
    casts, released = [], []
    monkeypatch.setattr(svc.clients, "cast_to_worker", lambda method, payload: casts.append(method))
    actions = importlib.import_module("services.compute.app.routers.server_actions")
    monkeypatch.setattr(actions, "release_allocation", lambda server_id: released.append(server_id))
    servers = importlib.import_module("services.compute.app.routers.servers")
    monkeypatch.setattr(servers, "_check_project_quota", lambda *a, **k: None)
    db = svc.SessionLocal()
    db.add(svc.Flavor(name="small", vcpus=1, ram_mb=512, disk_gb=1))
    db.commit()
    yield svc, TestClient(svc.app, raise_server_exceptions=False), casts, released
    db.close()


def _server(svc, status, **extra):
    db = svc.SessionLocal()
    db.add(svc.Server(id="srv1", name="vm1", project_id="p1", image_id="img1", root_volume_id="vol1", flavor="small",
                      network_id="net1", port_id="port1", host="node-1", status=status, **extra))
    db.commit()
    db.close()


def _status(svc):
    db = svc.SessionLocal()
    try:
        return db.get(svc.Server, "srv1").status
    finally:
        db.close()


def _no_override(monkeypatch):
    for key in PEERS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(catalog, "url", _down)


def test_delete_with_unresolvable_peers_is_502_and_changes_nothing(api, monkeypatch):
    svc, client, casts, released = api
    _server(svc, "ACTIVE")
    _no_override(monkeypatch)
    r = client.delete("/servers/srv1")
    assert r.status_code == 502 and "cannot resolve service" in r.json()["detail"]
    assert _status(svc) == "ACTIVE"
    assert released == [] and casts == []


def test_shelve_with_unresolvable_peers_is_502_and_changes_nothing(api, monkeypatch):
    svc, client, casts, _ = api
    _server(svc, "ACTIVE")
    _no_override(monkeypatch)
    r = client.post("/servers/srv1/shelve")
    assert r.status_code == 502 and "cannot resolve service" in r.json()["detail"]
    assert _status(svc) == "ACTIVE"
    assert casts == []


def test_unshelve_with_unresolvable_peers_is_502_and_changes_nothing(api, monkeypatch):
    svc, client, casts, _ = api
    _server(svc, "SHELVED_OFFLOADED")
    _no_override(monkeypatch)
    r = client.post("/servers/srv1/unshelve")
    assert r.status_code == 502 and "cannot resolve service" in r.json()["detail"]
    assert _status(svc) == "SHELVED_OFFLOADED"
    assert casts == []


def test_delete_still_works_when_the_peers_resolve(api, monkeypatch):
    svc, client, casts, released = api
    _server(svc, "ACTIVE")
    for key in PEERS:
        monkeypatch.setenv(key, "http://peer.test:1")
    assert client.delete("/servers/srv1").status_code < 400
    assert _status(svc) == "DELETING" and released == ["srv1"] and casts == ["delete_instance"]


def test_create_with_unresolvable_networking_is_502_not_500(api, monkeypatch):
    svc, client, casts, _ = api
    _no_override(monkeypatch)
    r = client.post("/servers", json={"name": "new", "flavor": "small", "network": "net1", "image": "img1"})
    assert r.status_code == 502 and "cannot resolve service 'network'" in r.json()["detail"]
    assert casts == []


def test_rebuild_with_unresolvable_image_service_is_502_and_changes_nothing(api, monkeypatch):
    svc, client, casts, _ = api
    _server(svc, "ACTIVE")
    _no_override(monkeypatch)
    r = client.post("/servers/srv1/rebuild", json={"image": "img2"})
    assert r.status_code == 502 and "cannot resolve service 'image'" in r.json()["detail"]
    assert _status(svc) == "ACTIVE"


def test_create_resolves_its_peers_before_claiming_anything(api, monkeypatch):
    """No placement claim and no ports are left behind when block-store cannot be resolved."""
    svc, client, casts, released = api
    for key in PEERS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("NETWORKING_API_URL", "http://peer.test:1")
    monkeypatch.setenv("IMAGE_API_URL", "http://peer.test:1")
    claims = []
    servers = importlib.import_module("services.compute.app.routers.servers")
    monkeypatch.setattr(servers, "claim_host", lambda *a, **k: claims.append(a) or "node-1")
    monkeypatch.setattr(svc.clients, "api_get", lambda url: {"id": "net1"})
    r = client.post("/servers", json={"name": "new", "flavor": "small", "network": "net1", "image": "img1"})
    assert r.status_code == 502 and "'volume'" in r.json()["detail"]
    assert claims == []


def test_a_plain_runtime_error_is_still_a_500(api, monkeypatch):
    svc, client, casts, _ = api
    _server(svc, "ACTIVE")
    for key in PEERS:
        monkeypatch.setenv(key, "http://peer.test:1")

    def boom(method, payload):
        raise RuntimeError("boom")

    monkeypatch.setattr(svc.clients, "cast_to_worker", boom)
    r = client.post("/servers/srv1/shelve")
    assert r.status_code == 500 and "boom" not in r.text
