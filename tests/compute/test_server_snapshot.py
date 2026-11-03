"""`cloudx server snapshot` asks block-store to snapshot the root volume and upload it as an image. It used to ask
image-api to import the snapshot from a path, which a volume-backed server never has (404 "source image path not
found"), and left the volume snapshot behind."""
from fastapi import HTTPException
from tests.compute import test_catalog_failures
from tests.compute.test_catalog_failures import PEERS

api, _server = test_catalog_failures.api, test_catalog_failures._server


def _wire(svc, monkeypatch, existing_images=()):
    for key in PEERS:
        monkeypatch.setenv(key, "http://peer.test:1")
    calls = []

    def image_get(url):
        calls.append(("get", url))
        if url.rsplit("/", 1)[-1] in existing_images:
            return {"id": "i1"}
        raise HTTPException(status_code=404, detail="image not found")

    monkeypatch.setattr(svc.clients, "api_get", image_get)
    monkeypatch.setattr(svc.clients, "api_post", lambda url, payload: calls.append(("post", url)) or {})
    monkeypatch.setattr(svc.clients, "block_post", lambda path, payload: calls.append(("block", path, payload)) or {"id": "s1", "status": "creating"})
    return calls


def test_the_root_volume_is_uploaded_as_an_image_of_the_servers_project(api, monkeypatch):
    svc, client, _, _ = api
    _server(svc, "ACTIVE")
    calls = _wire(svc, monkeypatch)

    r = client.post("/servers/srv1/snapshots", json={"name": "web-backup"})

    assert r.status_code == 202
    assert ("block", "/volumes/vol1/upload-image", {"image_name": "web-backup", "project_id": "p1"}) in calls
    assert r.json()["image"] == {"name": "web-backup", "status": "queued"}
    assert r.json()["snapshot"] == {"id": "s1", "status": "creating"}


def test_image_api_is_not_asked_to_import_from_a_path(api, monkeypatch):
    svc, client, _, _ = api
    _server(svc, "ACTIVE")
    calls = _wire(svc, monkeypatch)

    r = client.post("/servers/srv1/snapshots", json={"name": "web-backup"})

    assert r.status_code == 202
    assert not [c for c in calls if c[0] == "post"]


def test_a_name_that_an_image_already_has_is_409_before_anything_is_snapshotted(api, monkeypatch):
    svc, client, _, _ = api
    _server(svc, "ACTIVE")
    calls = _wire(svc, monkeypatch, existing_images={"web-backup"})

    r = client.post("/servers/srv1/snapshots", json={"name": "web-backup"})

    assert r.status_code == 409 and "already exists" in r.json()["detail"]
    assert not [c for c in calls if c[0] == "block"]


def test_a_server_without_a_root_volume_is_409(api, monkeypatch):
    svc, client, _, _ = api
    with svc.SessionLocal() as db:
        db.add(svc.Server(id="srv1", name="vm1", project_id="p1", image_id="img1", flavor="small", network_id="net1",
                          port_id="port1", host="node-1", status="ACTIVE"))
        db.commit()
    calls = _wire(svc, monkeypatch)

    assert client.post("/servers/srv1/snapshots", json={"name": "x"}).status_code == 409
    assert not [c for c in calls if c[0] == "block"]
