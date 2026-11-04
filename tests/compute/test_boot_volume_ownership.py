"""Booting from a volume needs the volume to belong to the caller's project (or to no project at all).

`clients.block_get` is stubbed to return the volume dict block-store serialises (it carries `project_id`); everything
else compute reaches out to is stubbed as in the image tests.
"""
import importlib

import pytest
import requests
from fastapi.testclient import TestClient
from shared.auth_middleware import require_auth
from tests.stack import fresh_service

TOKEN = "s3cret"
OWNER = {"project_id": "p1", "role": "member", "roles": ["member"], "sub": "u-owner", "scope_type": "project"}
OTHER = {"project_id": "p2", "role": "member", "roles": ["member"], "sub": "u-other", "scope_type": "project"}
DOMAIN_ADMIN = {"project_id": "p9", "role": "member", "roles": ["member"], "sub": "u-da", "scope_type": "project",
                "domain_admin": True, "domain_id": "d1"}
OTHER_DOMAIN_ADMIN = {**DOMAIN_ADMIN, "sub": "u-da2", "domain_id": "d2"}
ADMIN = {"project_id": "admin", "role": "admin", "roles": ["admin"], "sub": "u-admin", "scope_type": "project"}
INTERNAL = {"sub": "internal", "username": "admin", "project_id": "admin", "role": "admin", "roles": ["admin"],
            "scope_type": "system", "jti": "internal"}
PORT = {"id": "port1", "name": "lp-main", "mac_address": "02:00:00:00:00:01", "fixed_ip": "10.10.0.5"}


class Reply:
    def __init__(self, status_code, body):
        self.status_code, self._body = status_code, body

    def json(self):
        return self._body

    def raise_for_status(self):
        assert self.status_code < 400


def volume(**over):
    return {"id": "vol1", "name": "data", "status": "available", "bootable": True, "project_id": "p1",
            "image_id": "img0", **over}


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
    seen = {"claims": [], "released": [], "casts": [], "block_posts": [], "block_deletes": [], "block_gets": [],
            "ports": [], "api_deletes": []}
    current_volume = {"value": volume()}

    def fake_get(url, headers=None, timeout=None, **kw):
        if "/networks/" in url:
            return Reply(200, {"id": "net1", "name": "private-net"})
        if "/ports/" in url:
            return Reply(200, PORT)
        raise AssertionError(f"unexpected GET {url}")

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
    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(requests, "delete", fake_delete)
    monkeypatch.setattr(svc.clients, "block_get", lambda path: seen["block_gets"].append(path) or current_volume["value"])
    monkeypatch.setattr(svc.clients, "cast_to_worker", lambda method, payload: seen["casts"].append(method))
    monkeypatch.setattr(svc.clients, "api_post", lambda url, payload: seen["ports"].append(url) or {**PORT, "id": "port9"})
    monkeypatch.setattr(svc.clients, "api_delete", lambda url, payload=None: seen["api_deletes"].append(url))
    monkeypatch.setattr(svc.clients, "block_delete", lambda path: seen["block_deletes"].append(path))
    monkeypatch.setattr(svc.clients, "block_post", lambda path, payload: seen["block_posts"].append(path) or {"id": "root1"})
    monkeypatch.setattr(svc.clients, "_rpc", type("Rpc", (), {"publish_event": lambda *a, **k: None})())
    db = svc.SessionLocal()
    db.add(svc.Flavor(name="small", vcpus=1, ram_mb=512, disk_gb=1))
    db.commit()
    db.close()

    def as_(identity):
        who.clear()
        who.update(identity)

    def set_volume(**over):
        current_volume["value"] = volume(**over)

    def drop_project_id():
        current_volume["value"] = {k: v for k, v in volume().items() if k != "project_id"}

    return type("Env", (), {"svc": svc, "client": TestClient(svc.app), "seen": seen, "as_": staticmethod(as_),
                            "set_volume": staticmethod(set_volume), "drop_project_id": staticmethod(drop_project_id)})


def create(env):
    return env.client.post("/servers", json={"name": "vm1", "flavor": "small", "network": "net1", "boot_volume": "vol1"})


def server_rows(env):
    db = env.svc.SessionLocal()
    try:
        return db.query(env.svc.Server).count()
    finally:
        db.close()


def assert_nothing_was_created(env):
    seen = env.seen
    assert len(seen["claims"]) == 1, "the placement claim is made before the volume is looked up"
    assert seen["released"] == seen["claims"], "and released when the volume is refused"
    assert server_rows(env) == 0
    assert seen["ports"] == [] and seen["api_deletes"] == []
    assert seen["block_posts"] == [] and seen["block_deletes"] == []
    assert seen["casts"] == []


def test_create_server_refuses_a_boot_volume_of_another_project(env):
    env.as_(OTHER)

    r = create(env)

    assert r.status_code == 404
    assert r.json() == {"detail": "volume 'vol1' not found"}
    assert_nothing_was_created(env)


@pytest.mark.parametrize("over", [{"bootable": False}, {"status": "in-use"}], ids=["not_bootable", "not_available"])
def test_a_refused_boot_volume_is_not_reported_by_its_state(env, over):
    env.set_volume(**over)
    env.as_(OTHER)

    r = create(env)

    assert r.status_code == 404 and r.json() == {"detail": "volume 'vol1' not found"}
    assert_nothing_was_created(env)


def test_a_domain_admin_of_another_domain_is_refused(env):
    env.as_(OTHER_DOMAIN_ADMIN)

    r = create(env)

    assert r.status_code == 404 and r.json() == {"detail": "volume 'vol1' not found"}
    assert_nothing_was_created(env)


@pytest.mark.parametrize("who", [OWNER, DOMAIN_ADMIN, ADMIN, INTERNAL], ids=["owner", "domain_admin", "admin", "internal"])
def test_create_server_accepts_the_volume_of_the_callers_project_domain_or_an_admin(env, who):
    env.as_(who)

    r = create(env)

    assert r.status_code == 201, r.text
    assert r.json()["status"] == "BUILD"
    assert server_rows(env) == 1
    assert env.seen["casts"] == ["build_instance"]
    assert env.seen["released"] == [], "a server that is built keeps its placement claim"


def test_a_volume_without_an_owner_is_visible_to_everyone(env):
    env.drop_project_id()
    env.as_(OTHER)

    r = create(env)

    assert r.status_code == 201, r.text
    assert env.seen["casts"] == ["build_instance"]


def test_the_owner_still_gets_400_for_a_volume_that_is_not_bootable(env):
    env.set_volume(bootable=False)
    env.as_(OWNER)

    r = create(env)

    assert r.status_code == 400 and r.json() == {"detail": "boot volume must be bootable=true"}
    assert env.seen["released"] == env.seen["claims"]
    assert server_rows(env) == 0 and env.seen["casts"] == []


def test_the_owner_still_gets_409_for_a_volume_that_is_not_available(env):
    env.set_volume(status="in-use")
    env.as_(OWNER)

    r = create(env)

    assert r.status_code == 409
    assert r.json() == {"detail": "boot volume must be available; current status is in-use"}
    assert env.seen["released"] == env.seen["claims"]
    assert server_rows(env) == 0 and env.seen["casts"] == []
