"""Attaching or detaching a volume changes the server, so a locked server refuses it from a member. The compute API
checks the lock, but this route calls it with the internal token (admin), which passes."""
import importlib

import pytest
from fastapi import HTTPException
from tests.block_store import test_clone

block = test_clone.block
MEMBER = {"project_id": "proj-1", "role": "member", "sub": "u2"}
ADMIN = {"project_id": "proj-1", "role": "admin", "sub": "u1"}


@pytest.fixture
def routes(block, monkeypatch):
    svc, db, _ = block
    volumes = importlib.import_module("services.block_store.app.routers.volumes")
    posts = []

    class Ok:
        status_code = 200
        text = ""

    monkeypatch.setattr(volumes.requests, "post", lambda url, **kw: posts.append(url) or Ok())
    monkeypatch.setattr(volumes, "get_server", lambda ref: {"id": "srv1", "name": "vm", "status": "ACTIVE"})
    monkeypatch.setattr(volumes, "compute_api_url", lambda: "http://compute.test")
    return volumes, svc, db, posts


def _locked(monkeypatch, volumes, locked=True):
    monkeypatch.setattr(volumes, "server_locked", lambda server_id: locked)


def test_a_member_cannot_attach_a_volume_to_a_locked_server(routes, monkeypatch):
    volumes, svc, db, posts = routes
    _locked(monkeypatch, volumes)

    with pytest.raises(HTTPException) as e:
        volumes.attach_volume("src", svc.AttachIn(server="vm"), MEMBER, db)

    assert e.value.status_code == 409 and "locked" in e.value.detail
    assert posts == [] and db.get(svc.Volume, "v1").status == "available"


def test_a_member_cannot_detach_a_volume_from_a_locked_server(routes, monkeypatch):
    volumes, svc, db, posts = routes
    _locked(monkeypatch, volumes)
    volume = db.get(svc.Volume, "v1")
    volume.status, volume.attached_to_server_id = "in-use", "srv1"
    db.commit()

    with pytest.raises(HTTPException) as e:
        volumes.detach_volume("src", MEMBER, db)

    assert e.value.status_code == 409 and "locked" in e.value.detail
    assert posts == [] and db.get(svc.Volume, "v1").status == "in-use"


def test_an_admin_passes_the_lock(routes, monkeypatch):
    volumes, svc, db, posts = routes
    _locked(monkeypatch, volumes)

    volumes.attach_volume("src", svc.AttachIn(server="vm"), ADMIN, db)

    assert posts and db.get(svc.Volume, "v1").status == "in-use"


def test_a_member_can_attach_to_a_server_that_is_not_locked(routes, monkeypatch):
    volumes, svc, db, posts = routes
    _locked(monkeypatch, volumes, False)

    volumes.attach_volume("src", svc.AttachIn(server="vm"), MEMBER, db)

    assert posts
