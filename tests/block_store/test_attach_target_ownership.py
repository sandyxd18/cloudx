"""Attaching a volume through block-store needs the target server to be in the caller's project (or the caller to be an
admin, a service, or an admin of the server's domain). A server without an owner stays usable by everyone.

`get_server` is the real lookup, run against a `compute_servers` table in the test database.
"""
import importlib

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from tests.block_store import test_clone

block = test_clone.block
OWNER = {"project_id": "proj-1", "role": "member", "sub": "u-owner"}
OTHER = {"project_id": "p2", "role": "member", "sub": "u-other"}
ADMIN = {"project_id": "admin", "role": "admin", "sub": "u-admin"}
DOMAIN_ADMIN = {"project_id": "p9", "role": "member", "sub": "u-da", "domain_admin": True, "domain_id": "d1"}
OTHER_DOMAIN_ADMIN = {**DOMAIN_ADMIN, "domain_id": "d2"}  # sees the volume (proj-1) but not p2
INTERNAL = {"sub": "internal", "username": "admin", "project_id": "admin", "role": "admin", "roles": ["admin"],
            "scope_type": "system", "jti": "internal"}


@pytest.fixture
def routes(block, monkeypatch):
    svc, db, _ = block
    volumes = importlib.import_module("services.block_store.app.routers.volumes")
    posts = []

    class Ok:
        status_code = 200
        text = ""

    monkeypatch.setattr(volumes.requests, "post", lambda url, **kw: posts.append(url) or Ok())
    monkeypatch.setattr(volumes, "compute_api_url", lambda: "http://compute.test")
    monkeypatch.setattr(volumes, "server_locked", lambda server_id: False)
    monkeypatch.setattr("shared.domain.domain_projects", lambda d: {"d1": ["proj-1"], "d2": ["proj-1", "p9"]}.get(d, []))
    db.execute(text("CREATE TABLE compute_servers (id TEXT, name TEXT, status TEXT, project_id TEXT, locked BOOLEAN)"))
    db.execute(text("INSERT INTO compute_servers VALUES ('srv-own', 'mine', 'ACTIVE', 'proj-1', 0), "
                    "('srv-other', 'theirs', 'ACTIVE', 'p2', 0), ('srv-free', 'free', 'ACTIVE', NULL, 0)"))
    db.commit()
    return volumes, svc, db, posts


def attach(routes, server, who):
    volumes, svc, db, _ = routes
    return volumes.attach_volume("src", svc.AttachIn(server=server), who, db)


def test_the_real_server_lookup_returns_the_servers_project(routes):
    from services.block_store.app import clients

    assert clients.get_server("mine")["project_id"] == "proj-1"
    assert clients.get_server("srv-free")["project_id"] is None


@pytest.mark.parametrize("who", [OWNER, ADMIN, DOMAIN_ADMIN, INTERNAL], ids=["owner", "admin", "domain_admin", "internal"])
def test_a_volume_attaches_to_a_server_the_caller_may_see(routes, who):
    _, svc, db, posts = routes

    out = attach(routes, "mine", who)

    assert out["status"] == "in-use" and out["attached_to_server_id"] == "srv-own"
    assert posts == ["http://compute.test/servers/srv-own/volumes/attach"]


@pytest.mark.parametrize("who", [OWNER, OTHER], ids=["owner", "member_of_another_project"])
def test_a_server_without_an_owner_is_usable(routes, who):
    volume = routes[2].get(routes[1].Volume, "v1")
    volume.project_id = who["project_id"]
    routes[2].commit()

    assert attach(routes, "free", who)["status"] == "in-use"


@pytest.mark.parametrize("ref", ["theirs", "srv-other"])
@pytest.mark.parametrize("who", [OWNER, OTHER_DOMAIN_ADMIN], ids=["member", "admin_of_another_domain"])
def test_a_server_of_another_project_is_refused_before_anything_changes(routes, who, ref):
    _, svc, db, posts = routes

    with pytest.raises(HTTPException) as e:
        attach(routes, ref, who)

    assert e.value.status_code == 404 and e.value.detail == f"server '{ref}' not found"
    assert posts == [], "compute is not called"
    volume = db.get(svc.Volume, "v1")
    assert volume.status == "available" and volume.attached_to_server_id is None
