"""Shelve offloads a server (the worker drops the domain, the taps and the volume sessions, the records stay) and
unshelve builds the domain again the way evacuate does: placement picks the host, the volumes are released and attached
again, the ports are bound on the new node."""
import importlib

from tests.compute import test_catalog_failures
from tests.compute.test_catalog_failures import PEERS

api, _server, _status = test_catalog_failures.api, test_catalog_failures._server, test_catalog_failures._status

VOLUMES = {"items": [
    {"id": "vol1", "attached_to_server_id": "srv1"},
    {"id": "vol-data", "attached_to_server_id": "srv1", "target_device": "/dev/sdb"},
    {"id": "vol-other", "attached_to_server_id": "someone-else"},
]}


def _wire(svc, monkeypatch, order):
    for key in PEERS:
        monkeypatch.setenv(key, "http://peer.test:1")
    casts = []
    monkeypatch.setattr(svc.clients, "cast_to_worker", lambda method, payload: (order.append(method), casts.append((method, payload))))
    monkeypatch.setattr(svc.clients, "block_get", lambda path: VOLUMES if path == "/volumes" else {"id": "vol1"})
    monkeypatch.setattr(svc.clients, "api_get", lambda url: {"id": url.rsplit("/", 1)[-1], "mac_address": "00:11:22:33:44:55"})
    monkeypatch.setattr(svc.clients, "release_server_volumes", lambda sid: order.append(f"release:{sid}"))
    ops = importlib.import_module("services.compute.app.routers.ops")
    monkeypatch.setattr(ops, "claim_host", lambda *a, **k: "node-2")
    return casts, ops


def test_shelve_names_every_volume_the_worker_has_to_let_go_of(api, monkeypatch):
    svc, client, _, _ = api
    _server(svc, "ACTIVE")
    casts, _ = _wire(svc, monkeypatch, [])

    r = client.post("/servers/srv1/shelve")

    assert r.status_code == 200
    (method, payload), = casts
    assert method == "shelve" and payload["_root_volume_id"] == "vol1"
    assert payload["_volume_ids"] == ["vol1", "vol-data"]
    assert _status(svc) == "SHELVING"


def test_unshelve_builds_the_domain_on_the_host_placement_picks(api, monkeypatch):
    svc, client, _, _ = api
    _server(svc, "SHELVED_OFFLOADED")
    order = []
    casts, _ = _wire(svc, monkeypatch, order)

    r = client.post("/servers/srv1/unshelve")

    assert r.status_code in (200, 202)
    (method, build), = casts
    assert method == "build_instance"
    assert build["_root_volume_id"] == "vol1"
    assert build["_data_volumes"] == [{"id": "vol-data", "target_device": "sdb"}]
    assert build["_target_status"] == "ACTIVE"
    assert build["host"] == "node-2"
    assert order == ["release:srv1", "build_instance"], "the volumes are released before the new node attaches them"
    with svc.SessionLocal() as db:
        server = db.get(svc.Server, "srv1")
        assert (server.status, server.host) == ("UNSHELVING", "node-2")


def test_unshelve_with_no_host_to_take_it_is_409_and_the_server_stays_shelved(api, monkeypatch):
    svc, client, _, _ = api
    _server(svc, "SHELVED_OFFLOADED")
    order = []
    casts, ops = _wire(svc, monkeypatch, order)
    monkeypatch.setattr(ops, "claim_host", lambda *a, **k: None)
    monkeypatch.setattr(ops, "fallback_host", lambda *a, **k: None)

    r = client.post("/servers/srv1/unshelve")

    assert r.status_code == 409 and "No valid host" in r.json()["detail"]
    assert _status(svc) == "SHELVED_OFFLOADED"
    assert order == []


def test_unshelve_of_a_server_that_is_not_shelved_is_409(api, monkeypatch):
    svc, client, _, _ = api
    _server(svc, "ACTIVE")
    order = []
    _wire(svc, monkeypatch, order)

    assert client.post("/servers/srv1/unshelve").status_code == 409
    assert order == []


def _finish(svc, client, monkeypatch, released, **body):
    from shared.auth_middleware import require_internal
    svc.app.dependency_overrides[require_internal] = lambda: {"role": "internal"}
    ops = importlib.import_module("services.compute.app.routers.ops")
    monkeypatch.setattr(ops, "release_allocation", lambda sid: released.append(sid))
    return client.post("/internal/servers/srv1/actions/finish", json=body)


def test_a_finished_shelve_gives_the_placement_allocation_back(api, monkeypatch):
    svc, client, _, _ = api
    _server(svc, "SHELVED_OFFLOADED")
    released = []

    assert _finish(svc, client, monkeypatch, released, result="success", action="shelve").status_code == 200

    assert released == ["srv1"]


def test_a_failed_shelve_keeps_the_allocation(api, monkeypatch):
    svc, client, _, _ = api
    _server(svc, "ERROR")
    released = []

    _finish(svc, client, monkeypatch, released, result="error", message="boom", action="shelve")

    assert released == []


def test_other_finished_actions_keep_the_allocation(api, monkeypatch):
    svc, client, _, _ = api
    _server(svc, "ACTIVE")
    released = []

    _finish(svc, client, monkeypatch, released, result="success", action="reboot")
    _finish(svc, client, monkeypatch, released, result="success")

    assert released == []
