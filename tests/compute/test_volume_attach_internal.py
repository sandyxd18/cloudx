"""Volume attach and detach on a server are service-to-service routes: block-store calls them with the internal
token after it has checked the volume, the project and the lock. A member's token is refused, the internal token and
an admin token reach the worker.

Real auth chain (real JWTs, `INTERNAL_TOKEN` set): nothing is overridden on the compute app.
"""
import importlib
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from jose import jwt
from shared import auth_middleware as am
from tests.stack import fresh_service

TOKEN = "service-token"
INTERNAL = {"X-Internal-Token": TOKEN}
BODY = {"volume_id": "v1", "volume_name": "data", "path": "/dev/vg/data", "host": "node-1", "target_dev": "vdb"}
ROUTES = [("attach", "attaching", "ACTIVE"), ("detach", "detaching", "ACTIVE")]


def jwt_headers(role="member", project="p1"):
    claims = {"sub": "u1", "username": "u", "project_id": project, "role": role, "roles": [role],
              "scope_type": "project", "exp": datetime.now(timezone.utc) + timedelta(hours=1)}
    return {"X-Auth-Token": jwt.encode(claims, am.SECRET_KEY, algorithm=am.ALGORITHM)}


def add_server(svc, project="p1", status="ACTIVE", **extra):
    db = svc.SessionLocal()
    db.add(svc.Server(id="srv1", name="vm1", project_id=project, image_id="img1", root_volume_id="vol1", flavor="small",
                      network_id="net1", port_id="port1", host="node-1", status=status, **extra))
    db.commit()
    db.close()


@pytest.fixture
def compute(monkeypatch, tmp_path):
    monkeypatch.setenv("INTERNAL_TOKEN", TOKEN)
    monkeypatch.setenv("REVOCATION_CHECK", "false")
    svc = fresh_service("compute", monkeypatch, tmp_path)
    casts = []
    monkeypatch.setattr(svc.clients, "cast_to_worker", lambda method, payload: casts.append((method, payload)))
    db = svc.SessionLocal()
    db.add(svc.Flavor(name="small", vcpus=1, ram_mb=512, disk_gb=1))
    db.commit()
    db.close()
    add_server(svc)
    return svc, TestClient(svc.app, raise_server_exceptions=False), casts


@pytest.mark.parametrize("action,_status,_state", ROUTES)
def test_a_member_is_refused(compute, action, _status, _state):
    _, client, casts = compute

    r = client.post(f"/servers/srv1/volumes/{action}", json=BODY, headers=jwt_headers("member", "p1"))

    assert r.status_code in (401, 403), r.text
    assert casts == []


@pytest.mark.parametrize("action,_status,_state", ROUTES)
def test_a_request_without_credentials_is_refused(compute, action, _status, _state):
    _, client, casts = compute

    r = client.post(f"/servers/srv1/volumes/{action}", json=BODY)

    assert r.status_code in (401, 403), r.text
    assert casts == []


@pytest.mark.parametrize("action,_status,_state", ROUTES)
def test_a_wrong_internal_token_is_refused(compute, action, _status, _state):
    _, client, casts = compute

    r = client.post(f"/servers/srv1/volumes/{action}", json=BODY, headers={"X-Internal-Token": "wrong"})

    assert r.status_code in (401, 403), r.text
    assert casts == []


@pytest.mark.parametrize("action,status,_state", ROUTES)
def test_the_internal_token_reaches_the_worker_with_the_body_unchanged(compute, action, status, _state):
    _, client, casts = compute

    r = client.post(f"/servers/srv1/volumes/{action}", json=BODY, headers=INTERNAL)

    assert r.status_code == 200, r.text
    assert r.json() == {"status": status, "server_id": "srv1", "volume_id": "v1"}
    assert [(m, p["volume"]) for m, p in casts] == [(f"{action}_volume", BODY)]


@pytest.mark.parametrize("action,status,_state", ROUTES)
def test_an_admin_token_reaches_the_worker(compute, action, status, _state):
    _, client, casts = compute

    r = client.post(f"/servers/srv1/volumes/{action}", json=BODY, headers=jwt_headers("admin", "admin"))

    assert r.status_code == 200, r.text
    assert [(m, p["volume"]) for m, p in casts] == [(f"{action}_volume", BODY)]


@pytest.mark.parametrize("action,status,_state", ROUTES)
def test_the_internal_caller_finds_a_server_of_any_project(compute, action, status, _state):
    svc, client, casts = compute
    db = svc.SessionLocal()
    db.get(svc.Server, "srv1").project_id = "some-other-project"
    db.commit()
    db.close()

    r = client.post(f"/servers/srv1/volumes/{action}", json=BODY, headers=INTERNAL)

    assert r.status_code == 200, r.text
    assert len(casts) == 1


@pytest.mark.parametrize("action,status,_state", ROUTES)
def test_the_internal_caller_is_not_stopped_by_a_lock(compute, action, status, _state):
    """block-store refuses a member for a locked server before it calls; the service call itself passes."""
    svc, client, casts = compute
    db = svc.SessionLocal()
    server = db.get(svc.Server, "srv1")
    server.locked, server.locked_by = True, "alice"
    db.commit()
    db.close()

    r = client.post(f"/servers/srv1/volumes/{action}", json=BODY, headers=INTERNAL)

    assert r.status_code == 200, r.text
    assert len(casts) == 1


@pytest.mark.parametrize("action,_status,_state", ROUTES)
def test_an_unknown_server_is_404_for_the_internal_caller(compute, action, _status, _state):
    _, client, casts = compute

    r = client.post(f"/servers/nope/volumes/{action}", json=BODY, headers=INTERNAL)

    assert r.status_code == 404
    assert casts == []


def test_the_state_checks_still_apply(compute):
    svc, client, casts = compute
    db = svc.SessionLocal()
    db.get(svc.Server, "srv1").status = "ERROR"
    db.commit()
    db.close()

    assert client.post("/servers/srv1/volumes/attach", json=BODY, headers=INTERNAL).status_code == 409
    assert client.post("/servers/srv1/volumes/detach", json=BODY, headers=INTERNAL).status_code == 409
    assert casts == []


# ── block-store's own calls reach the compute routes ──────────────────────────────────────────────────────────────

MEMBER = {"project_id": "proj-1", "role": "member", "sub": "u2"}


@pytest.fixture
def both(compute, monkeypatch, tmp_path):
    """block-store's real attach and detach routes; its HTTP call goes, with the headers it built, to the real compute app."""
    csvc, cclient, casts = compute
    monkeypatch.setenv("STORAGE_LVM_DRIVER", "true")
    bsvc = fresh_service("block_store", monkeypatch, tmp_path)
    db = bsvc.SessionLocal()
    db.add(bsvc.VolumeType(id="vt", name="default", backend="lvm", is_public=True))
    db.add(bsvc.Volume(id="v1", name="data", size_gb=5, status="available", path="/dev/vg/data", backend="lvm",
                       project_id="proj-1", host="node-1"))
    db.commit()
    volumes = importlib.import_module("services.block_store.app.routers.volumes")
    seen = []

    def forward(url, json=None, headers=None, timeout=None, **kw):
        assert url.startswith("http://compute.test")
        seen.append(dict(headers or {}))
        return cclient.post(url[len("http://compute.test"):], json=json, headers=headers)

    monkeypatch.setattr(volumes.requests, "post", forward)
    monkeypatch.setattr(volumes, "compute_api_url", lambda: "http://compute.test")
    monkeypatch.setattr(volumes, "get_server", lambda ref: {"id": "srv1", "name": "vm1", "status": "ACTIVE"})
    monkeypatch.setattr(volumes, "server_locked", lambda server_id: False)
    yield volumes, bsvc, db, casts, seen
    db.close()


def test_block_stores_attach_reaches_the_compute_route(both):
    volumes, bsvc, db, casts, seen = both

    result = volumes.attach_volume("data", bsvc.AttachIn(server="vm1"), MEMBER, db)

    assert result["status"] == "in-use" and result["attached_to_server_id"] == "srv1"
    assert seen == [INTERNAL]
    assert len(casts) == 1 and casts[0][0] == "attach_volume"
    assert casts[0][1]["volume"]["volume_id"] == "v1" and casts[0][1]["volume"]["path"] == "/dev/vg/data"
    assert casts[0][1]["volume"]["target_dev"] == result["target_device"]


def test_block_stores_detach_reaches_the_compute_route(both):
    volumes, bsvc, db, casts, seen = both
    volume = db.get(bsvc.Volume, "v1")
    volume.status, volume.attached_to_server_id, volume.target_device = "in-use", "srv1", "vdb"
    db.commit()

    result = volumes.detach_volume("data", MEMBER, db)

    assert result["status"] == "available" and result["attached_to_server_id"] is None
    assert seen == [INTERNAL]
    assert len(casts) == 1 and casts[0][0] == "detach_volume"
    assert casts[0][1]["volume"]["volume_id"] == "v1" and casts[0][1]["volume"]["target_dev"] == "vdb"


def test_block_stores_attach_is_refused_when_compute_does_not_get_the_internal_token(both, monkeypatch):
    """The route only works with the token in place: without it the call fails and the volume stays available."""
    volumes, bsvc, db, casts, _ = both
    monkeypatch.setattr(volumes, "internal_headers", lambda: {})

    with pytest.raises(volumes.HTTPException) as e:
        volumes.attach_volume("data", bsvc.AttachIn(server="vm1"), MEMBER, db)

    assert e.value.status_code == 502
    assert casts == [] and db.get(bsvc.Volume, "v1").status == "available"
