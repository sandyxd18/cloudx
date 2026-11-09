"""A reference a user names (network, image, volume, port, snapshot name, project) goes into a peer URL as one path
segment: "." and ".." are 404 and nothing is sent to the peer; any other value is sent quoted as before."""
import pytest
from fastapi.testclient import TestClient
from shared.auth_middleware import require_auth
from tests.stack import fresh_service

ADMIN = {"role": "admin", "project_id": "p1", "sub": "u", "roles": ["admin"], "scope_type": "project"}
PORT = {"id": "port1", "name": "lp-main", "mac_address": "02:00:00:00:00:01", "fixed_ip": "10.10.0.5"}
NET = "http://networking.test"
IMG = "http://image.test"
DOTS = [".", ".."]


class Reply:
    def __init__(self, status_code=200, body=None):
        self.status_code, self._body, self.text = status_code, body or {}, ""

    def json(self):
        return self._body

    def raise_for_status(self):
        assert self.status_code < 400


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("INTERNAL_TOKEN", "s3cret")
    monkeypatch.setenv("REVOCATION_CHECK", "false")
    monkeypatch.setenv("IMAGE_API_URL", IMG)
    monkeypatch.setenv("NETWORKING_API_URL", NET)
    monkeypatch.setenv("BLOCK_STORE_API_URL", "http://block.test")
    monkeypatch.setenv("PLACEMENT_API_URL", "http://placement.test")
    svc = fresh_service("compute", monkeypatch, tmp_path)
    svc.app.dependency_overrides[require_auth] = lambda: ADMIN
    calls = []  # every request compute makes to a peer: (verb, url or path)

    def record(verb, answer):
        def call(url, *args, **kw):
            calls.append((verb, url))
            return answer(url) if callable(answer) else answer
        return call

    def peer_get(url):
        if "/ports/" in url:
            return PORT
        if "/volumes/" in url:
            return {"id": "vol9", "name": "v", "status": "available", "bootable": True, "project_id": "p1"}
        return {"id": "obj1", "name": "obj"}

    monkeypatch.setattr(svc.clients, "api_get", record("get", peer_get))
    monkeypatch.setattr(svc.clients, "api_post", record("post", {**PORT, "id": "port9"}))
    monkeypatch.setattr(svc.clients, "api_delete", record("delete", {}))
    monkeypatch.setattr(svc.clients, "block_get", record("block_get", peer_get))
    monkeypatch.setattr(svc.clients, "block_post", record("block_post", {"id": "root1"}))
    monkeypatch.setattr(svc.clients, "block_delete", record("block_delete", {}))
    monkeypatch.setattr(svc.clients, "cast_to_worker", lambda method, payload: calls.append(("cast", method)))
    monkeypatch.setattr(svc.clients, "_rpc", type("Rpc", (), {"publish_event": lambda *a, **k: None})())
    import requests
    monkeypatch.setattr(requests, "get", record("placement", Reply(200, {"compute_nodes": []})))
    monkeypatch.setattr(requests, "post", record("placement", Reply(200, {"host": "node-1"})))
    monkeypatch.setattr(requests, "delete", record("placement", Reply(200, {})))
    import importlib
    monkeypatch.setattr(importlib.import_module("services.compute.app.routers.servers"), "_check_project_quota", lambda *a, **k: None)
    with svc.SessionLocal() as db:
        db.add(svc.Flavor(name="small", vcpus=1, ram_mb=512, disk_gb=1))
        db.add(svc.Server(id="srv1", name="vm1", project_id="p1", image_id="img1", root_volume_id="vol1", flavor="small",
                          network_id="net1", port_id="port1", extra_port_ids="port2", host="node-1", status="ACTIVE"))
        db.commit()
    return type("Env", (), {"svc": svc, "client": TestClient(svc.app, raise_server_exceptions=False), "calls": calls})


def create(env, **over):
    return env.client.post("/servers", json={"name": "vm2", "flavor": "small", "network": "net1", "image": "img1", **over})


@pytest.mark.parametrize("ref", DOTS)
def test_a_dot_segment_as_the_network_is_404_and_nothing_is_sent(env, ref):
    r = create(env, network=ref)

    assert r.status_code == 404 and r.json() == {"detail": f"'{ref}' not found"}
    assert env.calls == []


@pytest.mark.parametrize("ref", DOTS)
def test_a_dot_segment_as_an_extra_network_is_404_and_nothing_is_sent(env, ref):
    r = create(env, networks=[ref])

    assert r.status_code == 404 and r.json() == {"detail": f"'{ref}' not found"}
    assert env.calls == []


@pytest.mark.parametrize("ref", DOTS)
def test_a_dot_segment_as_the_image_is_404_and_nothing_is_sent(env, ref):
    r = create(env, image=ref)

    assert r.status_code == 404 and r.json() == {"detail": f"'{ref}' not found"}
    assert env.calls == []


@pytest.mark.parametrize("ref", DOTS)
def test_a_dot_segment_as_the_boot_volume_is_404_and_nothing_is_sent(env, ref):
    r = create(env, image=None, boot_volume=ref)

    assert r.status_code == 404 and r.json() == {"detail": f"'{ref}' not found"}
    assert env.calls == []


def test_server_create_sends_each_reference_quoted_as_one_segment(env):
    r = create(env, network="my net", networks=["a/b"], image="img 1")

    assert r.status_code == 201, r.text
    gets = [c[1] for c in env.calls if c[0] == "get"]
    assert f"{NET}/networks/my%20net" in gets
    assert f"{NET}/networks/a%2Fb" in gets
    assert f"{IMG}/images/img%201" in gets


def test_server_create_sends_the_boot_volume_quoted_as_one_segment(env):
    r = create(env, image=None, boot_volume="vol/1")

    assert r.status_code == 201, r.text
    assert ("block_get", "/volumes/vol%2F1") in env.calls


@pytest.mark.parametrize("ref", DOTS)
def test_a_dot_segment_as_the_rebuild_image_is_404_and_nothing_is_sent(env, ref):
    r = env.client.post("/servers/srv1/rebuild", json={"image": ref})

    assert r.status_code == 404 and r.json() == {"detail": f"'{ref}' not found"}
    assert env.calls == []


@pytest.mark.parametrize("ref", DOTS)
def test_a_dot_segment_as_the_rescue_image_is_404_and_nothing_is_sent(env, ref):
    r = env.client.post("/servers/srv1/rescue", json={"image": ref})

    assert r.status_code == 404 and r.json() == {"detail": f"'{ref}' not found"}
    assert env.calls == []


def test_rebuild_and_rescue_send_the_image_quoted_as_one_segment(env):
    assert env.client.post("/servers/srv1/rebuild", json={"image": "img/2"}).status_code == 200
    assert env.client.post("/servers/srv1/rescue", json={"image": "img 3"}).status_code == 200

    gets = [c[1] for c in env.calls if c[0] == "get"]
    assert f"{IMG}/images/img%2F2" in gets and f"{IMG}/images/img%203" in gets


@pytest.mark.parametrize("ref", DOTS)
def test_a_dot_segment_as_the_snapshot_name_is_404_and_nothing_is_sent(env, ref):
    r = env.client.post("/servers/srv1/snapshots", json={"name": ref})

    assert r.status_code == 404 and r.json() == {"detail": f"'{ref}' not found"}
    assert env.calls == []


def test_the_snapshot_name_is_sent_quoted_as_one_segment(env, monkeypatch):
    def image_get(url, **kw):
        env.calls.append(("get", url))
        raise env.svc.clients.HTTPException(status_code=404, detail="image not found")

    monkeypatch.setattr(env.svc.clients, "api_get", image_get)

    r = env.client.post("/servers/srv1/snapshots", json={"name": "web/backup"})

    assert r.status_code == 202, r.text
    assert ("get", f"{IMG}/images/web%2Fbackup") in env.calls


@pytest.mark.parametrize("ref", DOTS)
def test_a_dot_segment_as_the_port_to_add_is_404_and_nothing_is_sent(env, ref):
    r = env.client.post("/servers/srv1/ports", json={"port": ref})

    assert r.status_code == 404 and r.json() == {"detail": f"'{ref}' not found"}
    assert env.calls == []


def test_the_port_to_add_is_sent_quoted_as_one_segment(env):
    env.client.post("/servers/srv1/ports", json={"port": "p/9"})

    assert ("get", f"{NET}/ports/p%2F9") in env.calls


@pytest.mark.parametrize("ref", DOTS)
def test_a_dot_segment_as_the_port_to_remove_is_404_and_nothing_is_sent(env, ref):
    encoded = ref.replace(".", "%2E")  # the client would otherwise fold the dots into the parent path itself
    r = env.client.request("DELETE", f"/servers/srv1/ports/{encoded}")

    assert r.status_code == 404
    assert env.calls == []


def test_the_port_to_remove_is_sent_quoted_as_one_segment(env):
    env.client.request("DELETE", "/servers/srv1/ports/p%209")

    assert ("get", f"{NET}/ports/p%209") in env.calls


@pytest.fixture
def quota_calls(env, monkeypatch):
    calls = []

    def request(method, url, **kw):
        calls.append((method, url))
        return Reply(200, {"project_id": "x", "max_instances": 1, "max_vcpus": 1, "max_ram_mb": 1})

    monkeypatch.setattr(env.svc.ops.requests, "request", request)
    return calls


@pytest.mark.parametrize("ref", DOTS)
def test_a_dot_segment_as_the_project_of_a_quota_set_is_404_and_nothing_is_sent(env, quota_calls, ref):
    encoded = ref.replace(".", "%2E")
    got = env.client.get(f"/os-quota-sets/{encoded}")
    put = env.client.put(f"/os-quota-sets/{encoded}", json={"quota_set": {"instances": 2}})

    assert got.status_code == 404 and put.status_code == 404
    assert got.json() == {"detail": f"'{ref}' not found"}
    assert quota_calls == []


def test_the_project_of_a_quota_set_is_sent_quoted_as_one_segment(env, quota_calls):
    env.client.get("/os-quota-sets/p1")
    env.client.put("/os-quota-sets/p%201", json={"quota_set": {"instances": 2}})

    assert [url.rsplit("/projects/", 1)[1] for _, url in quota_calls] == ["p1/quota", "p%201/quota"]
