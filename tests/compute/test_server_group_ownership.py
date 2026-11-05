"""A server group named by id needs to belong to the caller's project (or to none), like one named by name.

Same visibility rule as the name lookup, plus the system admin and the domain admin of the group's domain.
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
        db.add(svc.Flavor(name="small", vcpus=1, ram_mb=512, disk_gb=1))
        db.add_all([
            svc.ServerGroup(id="sg-p1", name="grp", project_id="p1", policy="anti-affinity"),
            svc.ServerGroup(id="sg-none", name="unowned", project_id=None, policy="anti-affinity"),
        ])
        db.commit()

    def as_(identity):
        who.clear()
        who.update(identity)

    return type("Env", (), {"svc": svc, "client": TestClient(svc.app), "seen": seen, "as_": staticmethod(as_)})


def create(env, group):
    return env.client.post("/servers", json={"name": "vm1", "flavor": "small", "network": "net1",
                                             "boot_volume": "vol1", "server_group": group})


def server_rows(env):
    with env.svc.SessionLocal() as db:
        return db.query(env.svc.Server).count()


def group_exists(env, group_id):
    with env.svc.SessionLocal() as db:
        return db.get(env.svc.ServerGroup, group_id) is not None


ALLOWED = {"owner": OWNER, "domain_admin": DOMAIN_ADMIN, "admin": ADMIN, "internal": INTERNAL}
REFUSED = {"other_project": OTHER, "other_domain_admin": OTHER_DOMAIN_ADMIN}


@pytest.mark.parametrize("case", REFUSED)
def test_create_server_refuses_a_server_group_of_another_project(env, case):
    env.as_(REFUSED[case])

    r = create(env, "sg-p1")

    assert r.status_code == 404 and r.json() == {"detail": "server group not found"}
    assert server_rows(env) == 0 and env.seen["claims"] == [] and env.seen["ports"] == [] and env.seen["casts"] == []


@pytest.mark.parametrize("case", ALLOWED)
def test_create_server_accepts_a_server_group_the_caller_can_see(env, case):
    env.as_(ALLOWED[case])

    r = create(env, "sg-p1")

    assert r.status_code == 201, r.text
    assert r.json()["server_group_id"] == "sg-p1"


def test_create_server_accepts_a_server_group_without_a_project(env):
    env.as_(OTHER)

    r = create(env, "sg-none")

    assert r.status_code == 201, r.text
    assert r.json()["server_group_id"] == "sg-none"


def test_create_server_still_finds_a_group_of_the_callers_project_by_name(env):
    env.as_(OWNER)

    assert create(env, "grp").status_code == 201


def test_create_server_does_not_find_a_group_of_another_project_by_name(env):
    env.as_(OTHER)

    r = create(env, "grp")

    assert r.status_code == 404 and r.json() == {"detail": "server group not found"}


@pytest.mark.parametrize("case", REFUSED)
def test_show_and_delete_refuse_a_server_group_of_another_project(env, case):
    env.as_(REFUSED[case])

    shown = env.client.get("/server-groups/sg-p1")
    deleted = env.client.delete("/server-groups/sg-p1")

    assert shown.status_code == 404 and shown.json() == {"detail": "server group not found"}
    assert deleted.status_code == 404 and deleted.json() == {"detail": "server group not found"}
    assert group_exists(env, "sg-p1")


@pytest.mark.parametrize("case", ["owner", "domain_admin", "admin"])
def test_show_and_delete_accept_a_server_group_the_caller_can_see(env, case):
    env.as_(ALLOWED[case])

    shown = env.client.get("/server-groups/sg-p1")
    deleted = env.client.delete("/server-groups/sg-p1")

    assert shown.status_code == 200 and shown.json()["id"] == "sg-p1"
    assert deleted.status_code == 204
    assert not group_exists(env, "sg-p1")


def test_show_and_delete_accept_a_server_group_without_a_project(env):
    env.as_(OTHER)

    assert env.client.get("/server-groups/sg-none").status_code == 200
    assert env.client.delete("/server-groups/sg-none").status_code == 204


def test_show_and_delete_by_name_stay_in_the_callers_project(env):
    env.as_(OTHER)
    assert env.client.get("/server-groups/grp").status_code == 404
    assert env.client.delete("/server-groups/grp").status_code == 404
    assert group_exists(env, "sg-p1")

    env.as_(OWNER)
    assert env.client.get("/server-groups/grp").status_code == 200
    assert env.client.delete("/server-groups/grp").status_code == 204
