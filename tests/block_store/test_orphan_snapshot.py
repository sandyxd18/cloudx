"""A snapshot follows its volume's project; one whose volume row is gone is shown to admins and the internal caller only.

The routes are driven through a TestClient with the real auth dependencies; the worker casts are captured.
"""
import pytest
import requests
from fastapi.testclient import TestClient
from shared.auth_middleware import require_auth
from tests.stack import fresh_service

TOKEN = "s3cret"
OWNER = {"project_id": "p1", "role": "member", "roles": ["member"], "sub": "u-owner", "scope_type": "project"}
OTHER = {"project_id": "p2", "role": "member", "roles": ["member"], "sub": "u-other", "scope_type": "project"}
ADMIN = {"project_id": "admin", "role": "admin", "roles": ["admin"], "sub": "u-admin", "scope_type": "project"}


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("INTERNAL_TOKEN", TOKEN)
    monkeypatch.setenv("REVOCATION_CHECK", "false")
    monkeypatch.setenv("STORAGE_LVM_DRIVER", "true")
    monkeypatch.setattr("shared.domain.domain_projects", lambda d: [])
    svc = fresh_service("block_store", monkeypatch, tmp_path)
    who = {}
    svc.app.dependency_overrides[require_auth] = lambda: dict(who)
    casts = []

    def no_auth_api(url, **kw):
        raise requests.ConnectionError("auth-api is not part of this test")

    monkeypatch.setattr(requests, "get", no_auth_api)
    monkeypatch.setattr(svc.clients, "cast_to_worker", lambda method, payload: casts.append((method, payload)))
    monkeypatch.setattr(svc.clients, "_rpc", type("Rpc", (), {"publish_event": lambda *a, **k: None})())
    with svc.SessionLocal() as db:
        db.add(svc.VolumeType(id="vt", name="default", backend="lvm", is_public=True))
        db.add(svc.Volume(id="v1", name="src", size_gb=2, status="available", path="/dev/vg/src", backend="lvm",
                          project_id="p1"))
        db.add(svc.Snapshot(id="s1", name="snap", volume_id="v1", size_gb=2, status="available", path="/dev/vg/snap",
                            backend="lvm"))
        db.add(svc.Snapshot(id="s-gone", name="gone", volume_id="v-gone", size_gb=2, status="available",
                            path="/dev/vg/gone", backend="lvm"))
        db.commit()

    def as_(identity):
        who.clear()
        who.update(identity)

    as_(OWNER)
    return type("Env", (), {"svc": svc, "client": TestClient(svc.app), "casts": casts, "as_": staticmethod(as_)})


def refused(ref):
    return {"detail": f"snapshot '{ref}' not found"}


@pytest.mark.parametrize("ref", ["s-gone", "gone"])
def test_a_member_does_not_see_a_snapshot_whose_volume_is_gone(env, ref):
    r = env.client.get(f"/snapshots/{ref}")

    assert r.status_code == 404 and r.json() == refused(ref)


@pytest.mark.parametrize("ref", ["s-gone", "gone"])
def test_a_member_does_not_delete_a_snapshot_whose_volume_is_gone(env, ref):
    r = env.client.delete(f"/snapshots/{ref}")

    assert r.status_code == 404 and r.json() == refused(ref)
    assert env.casts == []
    with env.svc.SessionLocal() as db:
        assert db.get(env.svc.Snapshot, "s-gone").status == "available"


@pytest.mark.parametrize("ref", ["s-gone", "gone"])
def test_a_member_does_not_clone_a_snapshot_whose_volume_is_gone(env, ref):
    r = env.client.post("/volumes", json={"name": "c", "size_gb": 2, "snapshot": ref})

    assert r.status_code == 404 and r.json() == refused(ref)
    assert env.casts == []
    with env.svc.SessionLocal() as db:
        assert db.query(env.svc.Volume).count() == 1


def test_a_member_does_not_revert_to_a_snapshot_whose_volume_is_gone(env):
    r = env.client.post("/volumes/src/action", json={"revert": {"snapshot_id": "s-gone"}})

    assert r.status_code == 404 and r.json() == refused("s-gone")
    assert env.casts == []


def test_a_member_does_not_list_a_snapshot_whose_volume_is_gone(env):
    names = {s["name"] for s in env.client.get("/snapshots").json()["items"]}

    assert names == {"snap"}


def test_an_admin_sees_a_snapshot_whose_volume_is_gone(env):
    env.as_(ADMIN)

    assert env.client.get("/snapshots/s-gone").json()["name"] == "gone"
    assert env.client.get("/snapshots/gone").status_code == 200
    assert {s["name"] for s in env.client.get("/snapshots").json()["items"]} == {"snap", "gone"}


def test_an_admin_deletes_a_snapshot_whose_volume_is_gone(env):
    env.as_(ADMIN)

    r = env.client.delete("/snapshots/s-gone")

    assert r.status_code == 200 and env.casts[-1][0] == "delete_snapshot"


def test_an_admin_clones_a_snapshot_whose_volume_is_gone(env):
    env.as_(ADMIN)

    r = env.client.post("/volumes", json={"name": "c", "size_gb": 2, "snapshot": "s-gone"})

    assert r.status_code == 201, r.text


def test_the_internal_lookup_finds_a_snapshot_whose_volume_is_gone(env):
    with env.svc.SessionLocal() as db:
        assert env.svc.lookups.require_snapshot(db, "s-gone", None).id == "s-gone"


def test_a_snapshot_of_another_projects_volume_stays_refused(env):
    env.as_(OTHER)

    r = env.client.get("/snapshots/s1")

    assert r.status_code == 404 and r.json() == refused("s1")


def test_a_snapshot_of_the_callers_volume_stays_visible(env):
    assert env.client.get("/snapshots/s1").status_code == 200
