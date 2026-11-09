"""A flavor that is not public is listed and usable by admins (and the internal caller) only.

The routes are driven through a TestClient with the real lookups; the placement claim, the ports and the worker casts
are captured. A server that already uses such a flavor keeps working for its owner.
"""
import importlib

import pytest
import requests
from fastapi.testclient import TestClient
from shared.auth_middleware import require_auth
from tests.stack import fresh_service

TOKEN = "s3cret"
OWNER = {"project_id": "p1", "role": "member", "roles": ["member"], "sub": "u-owner", "scope_type": "project"}
ADMIN = {"project_id": "admin", "role": "admin", "roles": ["admin"], "sub": "u-admin", "scope_type": "project"}
INTERNAL = {"sub": "internal", "username": "admin", "project_id": "admin", "role": "admin", "roles": ["admin"],
            "scope_type": "system", "jti": "internal"}
PORT = {"id": "port1", "name": "lp-main", "mac_address": "02:00:00:00:00:01", "fixed_ip": "10.10.0.5"}
VOLUME = {"id": "vol1", "name": "data", "status": "available", "bootable": True, "project_id": None, "image_id": "img0"}


class Reply:
    def __init__(self, status_code, body):
        self.status_code, self._body = status_code, body

    def json(self):
        return self._body

    def raise_for_status(self):
        assert self.status_code < 400


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("INTERNAL_TOKEN", TOKEN)
    monkeypatch.setenv("REVOCATION_CHECK", "false")
    for key in ("NETWORKING_API_URL", "BLOCK_STORE_API_URL", "PLACEMENT_API_URL"):
        monkeypatch.setenv(key, f"http://{key.split('_')[0].lower()}.test")
    monkeypatch.setattr("shared.domain.domain_projects", lambda d: ["p1"] if d == "d1" else [])

    svc = fresh_service("compute", monkeypatch, tmp_path)
    who = {}
    svc.app.dependency_overrides[require_auth] = lambda: who
    seen = {"claims": [], "released": [], "casts": [], "ports": []}

    def fake_post(url, headers=None, json=None, timeout=None, **kw):  # placement claim
        assert url.endswith("/internal/claim"), url
        seen["claims"].append(json["consumer_uuid"])
        return Reply(200, {"host": "node-1"})

    def fake_delete(url, headers=None, timeout=None, **kw):  # placement release
        assert "/internal/allocations/" in url, url
        seen["released"].append(url.rsplit("/", 1)[1])
        return Reply(200, {})

    servers = importlib.import_module("services.compute.app.routers.servers")
    monkeypatch.setattr(servers, "_check_project_quota", lambda *a, **k: None)
    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(requests, "delete", fake_delete)
    monkeypatch.setattr(svc.clients, "api_get", lambda url, caller=None: {"id": "net1", "name": "private-net"})
    monkeypatch.setattr(svc.clients, "api_post", lambda url, payload, caller=None: seen["ports"].append(url) or PORT)
    monkeypatch.setattr(svc.clients, "api_delete", lambda url, payload=None: None)
    monkeypatch.setattr(svc.clients, "block_get", lambda path: dict(VOLUME))
    monkeypatch.setattr(svc.clients, "cast_to_worker", lambda method, payload: seen["casts"].append(method))
    monkeypatch.setattr(svc.clients, "_rpc", type("Rpc", (), {"publish_event": lambda *a, **k: None})())
    with svc.SessionLocal() as db:
        db.add_all([
            svc.Flavor(id="f-small", name="small", vcpus=1, ram_mb=512, disk_gb=1),
            svc.Flavor(id="f-big", name="big", vcpus=2, ram_mb=1024, disk_gb=2),
            svc.Flavor(id="f-hidden", name="hidden", vcpus=4, ram_mb=2048, disk_gb=4, is_public=False,
                       extra_specs='{"k": "v"}'),
        ])
        db.commit()

    def as_(identity):
        who.clear()
        who.update(identity)

    return type("Env", (), {"svc": svc, "client": TestClient(svc.app), "seen": seen, "as_": staticmethod(as_)})


def create(env, flavor, name="vm1"):
    return env.client.post("/servers", json={"name": name, "flavor": flavor, "network": "net1", "boot_volume": "vol1"})


def server_rows(env):
    with env.svc.SessionLocal() as db:
        return db.query(env.svc.Server).count()


def add_server(env, flavor="hidden", status="ACTIVE"):
    with env.svc.SessionLocal() as db:
        db.add(env.svc.Server(id="srv1", name="old", project_id="p1", image_id="img1", root_volume_id="vol1",
                              network_id="net1", port_id="port1", flavor=flavor, flavor_name=flavor, vcpus=4,
                              ram_mb=2048, disk_gb=4, status=status, backend="lvm"))
        db.commit()


def refused(ref):
    return {"detail": f"flavor '{ref}' not found"}


def test_a_member_lists_the_public_flavors_only(env):
    env.as_(OWNER)

    names = {f["name"] for f in env.client.get("/flavors").json()["items"]}

    assert names == {"small", "big"}


@pytest.mark.parametrize("who", [ADMIN, INTERNAL])
def test_an_admin_lists_every_flavor(env, who):
    env.as_(who)

    names = {f["name"] for f in env.client.get("/flavors").json()["items"]}

    assert names == {"small", "big", "hidden"}


@pytest.mark.parametrize("ref", ["f-hidden", "hidden"])
@pytest.mark.parametrize("suffix", ["", "/os-extra_specs"])
def test_a_member_does_not_see_a_flavor_that_is_not_public(env, ref, suffix):
    env.as_(OWNER)

    r = env.client.get(f"/flavors/{ref}{suffix}")

    assert r.status_code == 404 and r.json() == refused(ref)


@pytest.mark.parametrize("ref", ["f-hidden", "hidden"])
def test_an_admin_sees_a_flavor_that_is_not_public(env, ref):
    env.as_(ADMIN)

    assert env.client.get(f"/flavors/{ref}").json()["name"] == "hidden"
    assert env.client.get(f"/flavors/{ref}/os-extra_specs").json() == {"extra_specs": {"k": "v"}}


def test_a_member_sees_a_public_flavor(env):
    env.as_(OWNER)

    assert env.client.get("/flavors/small").json()["name"] == "small"
    assert env.client.get("/flavors/f-big/os-extra_specs").status_code == 200


@pytest.mark.parametrize("ref", ["f-hidden", "hidden"])
def test_create_server_refuses_a_flavor_that_is_not_public_for_a_member(env, ref):
    env.as_(OWNER)

    r = create(env, ref)

    assert r.status_code == 404 and r.json() == refused(ref)
    assert server_rows(env) == 0 and env.seen["claims"] == [] and env.seen["ports"] == [] and env.seen["casts"] == []


@pytest.mark.parametrize("who", [ADMIN, INTERNAL])
def test_create_server_accepts_a_flavor_that_is_not_public_for_an_admin(env, who):
    env.as_(who)

    r = create(env, "hidden")

    assert r.status_code == 201, r.text
    assert r.json()["flavor"] == "hidden"
    assert env.seen["claims"] and env.seen["casts"]


def test_create_server_accepts_a_public_flavor_for_a_member(env):
    env.as_(OWNER)

    assert create(env, "small").status_code == 201


@pytest.mark.parametrize("ref", ["f-hidden", "hidden"])
def test_resize_refuses_a_flavor_that_is_not_public_for_a_member(env, ref):
    add_server(env, flavor="small")
    env.as_(OWNER)

    r = env.client.post("/servers/old/resize", json={"flavor": ref})

    assert r.status_code == 404 and r.json() == refused(ref)
    assert env.seen["casts"] == []
    with env.svc.SessionLocal() as db:
        server = db.get(env.svc.Server, "srv1")
        assert (server.flavor, server.status, server.original_flavor) == ("small", "ACTIVE", None)


def test_resize_accepts_a_flavor_that_is_not_public_for_an_admin(env):
    add_server(env, flavor="small")
    env.as_(ADMIN)

    r = env.client.post("/servers/old/resize", json={"flavor": "hidden"})

    assert r.status_code == 200, r.text
    assert r.json()["new_flavor"] == "hidden" and env.seen["casts"] == ["resize"]


def test_resize_accepts_a_public_flavor_for_a_member(env):
    add_server(env, flavor="small")
    env.as_(OWNER)

    assert env.client.post("/servers/old/resize", json={"flavor": "big"}).status_code == 200


def test_a_server_on_a_flavor_that_is_not_public_keeps_working_for_its_owner(env):
    add_server(env, flavor="hidden", status="ACTIVE")
    env.as_(OWNER)

    shown = env.client.get("/servers/old")
    stopped = env.client.post("/servers/old/stop")

    assert shown.status_code == 200 and shown.json()["flavor"] == "hidden", shown.text
    assert stopped.status_code == 200 and env.seen["casts"] == ["stop"]


def test_the_owner_reverts_a_resize_to_a_flavor_that_is_not_public(env):
    add_server(env, flavor="big", status="VERIFY_RESIZE")
    with env.svc.SessionLocal() as db:
        server = db.get(env.svc.Server, "srv1")
        server.original_flavor, server.original_vcpus, server.original_ram_mb = "hidden", 4, 2048
        db.commit()
    env.as_(OWNER)

    r = env.client.post("/servers/old/revertResize")

    assert r.status_code == 200, r.text
    assert r.json()["flavor"] == "hidden" and r.json()["flavor_name"] == "hidden"
