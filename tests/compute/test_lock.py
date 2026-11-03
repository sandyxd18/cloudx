"""A locked server refuses every action that changes it when the caller is a member. Only delete, shelve and unshelve
checked the lock, so a member could stop, reboot, resize or rebuild a server that was locked against exactly that.
Admins and services (the internal token acts as admin) pass, as in Nova."""
import pytest
from shared.auth_middleware import require_auth
from tests.compute import test_catalog_failures

api, _server, _status = test_catalog_failures.api, test_catalog_failures._server, test_catalog_failures._status

MEMBER = {"role": "member", "project_id": "p1", "sub": "u2", "username": "bob"}

# (method, path, body) of every call that changes a server
ACTIONS = [
    ("POST", "/servers/srv1/start", None),
    ("POST", "/servers/srv1/stop", None),
    ("POST", "/servers/srv1/reboot", {"type": "soft"}),
    ("POST", "/servers/srv1/pause", None),
    ("POST", "/servers/srv1/resume", None),
    ("POST", "/servers/srv1/suspend", None),
    ("POST", "/servers/srv1/unsuspend", None),
    ("POST", "/servers/srv1/resize", {"flavor": "small"}),
    ("POST", "/servers/srv1/confirmResize", None),
    ("POST", "/servers/srv1/revertResize", None),
    ("POST", "/servers/srv1/rebuild", {"image": "img1"}),
    ("POST", "/servers/srv1/rescue", {}),
    ("POST", "/servers/srv1/unrescue", None),
    ("POST", "/servers/srv1/password", {"password": "x"}),
    ("POST", "/servers/srv1/ports", {"port": "p9"}),
    ("DELETE", "/servers/srv1/ports/p9", None),
    ("POST", "/servers/srv1/volumes/attach", {"volume_id": "v1", "path": "/dev/x"}),
    ("POST", "/servers/srv1/volumes/detach", {"volume_id": "v1", "path": "/dev/x"}),
    ("POST", "/servers/srv1/shelve", None),
    ("POST", "/servers/srv1/unshelve", None),
    ("DELETE", "/servers/srv1", None),
]


@pytest.fixture
def locked(api):
    svc, client, casts, released = api
    _server(svc, "ACTIVE", locked=True, locked_by="alice")
    svc.app.dependency_overrides[require_auth] = lambda: MEMBER
    return svc, client, casts, released


@pytest.mark.parametrize("method,path,body", ACTIONS, ids=[f"{m} {p.replace('/servers/srv1', '') or '/'}" for m, p, _ in ACTIONS])
def test_a_member_cannot_change_a_locked_server(locked, method, path, body):
    svc, client, casts, released = locked

    r = client.request(method, path, json=body)

    assert r.status_code == 409 and "locked" in r.json()["detail"], r.text
    assert casts == [] and released == []
    assert _status(svc) == "ACTIVE"


def test_an_admin_passes_the_lock(locked):
    svc, client, casts, _ = locked
    svc.app.dependency_overrides[require_auth] = lambda: test_catalog_failures.ADMIN

    assert client.post("/servers/srv1/stop").status_code == 200
    assert casts == ["stop"]


def test_a_member_can_still_use_a_server_that_is_not_locked(api):
    svc, client, casts, _ = api
    _server(svc, "ACTIVE")
    svc.app.dependency_overrides[require_auth] = lambda: MEMBER

    assert client.post("/servers/srv1/stop").status_code == 200
    assert casts == ["stop"]


def test_a_member_can_read_and_unlock_their_own_lock_on_a_locked_server(locked):
    svc, client, _, _ = locked
    svc.app.dependency_overrides[require_auth] = lambda: {**MEMBER, "username": "alice", "sub": "u1"}

    assert client.get("/servers/srv1").status_code == 200
    assert client.post("/servers/srv1/unlock").status_code == 200
