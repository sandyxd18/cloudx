"""The 6 admin-only routes and 2 admin-only volume sub-actions of block-store are gated by the central policy."""
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "services"))

from shared import policy  # noqa: E402
from shared.auth_middleware import require_auth  # noqa: E402
from tests.gates import assert_gated, route_actions  # noqa: E402
from tests.stack import fresh_service  # noqa: E402

EXPECTED = {
    ("POST", "/volume-types"): "block-store:volume-type:create",
    ("PATCH", "/volume-types/{ref}"): "block-store:volume-type:update",
    ("DELETE", "/volume-types/{ref}"): "block-store:volume-type:delete",
    ("PUT", "/os-quota-sets/{project_id}"): "block-store:quota:set",
    ("DELETE", "/os-quota-sets/{project_id}"): "block-store:quota:clear",
    ("POST", "/volumes/{ref}/force-detach"): "block-store:volume:force-detach",
}
ROUTES = [(method, path, action) for (method, path), action in EXPECTED.items()]
SUB_ACTIONS = [
    ({"os-force_delete": {}}, "block-store:volume:force-delete"),
    ({"os-reset_status": {"status": "error"}}, "block-store:volume:reset-status"),
]


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def pfile(tmp_path, monkeypatch):
    """A policy file that does not exist yet; every write gets a distinct mtime so tests never sleep."""
    path = tmp_path / "policy.yaml"
    monkeypatch.setenv("CLOUDX_POLICY_FILE", str(path))
    clock = Clock()
    monkeypatch.setattr(policy, "_monotonic", clock)
    policy.reset()

    class PolicyFile:
        tick = 0

        def write(self, text):
            path.write_text(text, encoding="utf-8")
            self.tick += 1
            stamp = 10**18 + self.tick * 10**9
            os.utime(path, ns=(stamp, stamp))
            clock.advance(policy.CHECK_INTERVAL + 1)

    yield PolicyFile()
    policy.reset()


@pytest.fixture
def block(monkeypatch, tmp_path, pfile):
    monkeypatch.setenv("VOLUME_STORE_PATH", str(tmp_path / "volumes"))
    svc = fresh_service("block_store", monkeypatch, tmp_path)
    who = {"sub": "u", "role": "admin", "project_id": "p1", "domain_admin": False}
    svc.app.dependency_overrides[require_auth] = lambda: who
    svc.who = who
    with svc.SessionLocal() as db:
        db.add(svc.Volume(id="v-own", name="own", size_gb=1, status="available", project_id="p1"))
        db.add(svc.Volume(id="v-other", name="other", size_gb=1, status="available", project_id="p2"))
        db.add(svc.VolumeType(id="vt1", name="gold", backend="lvm", disk_type="sata_hdd", is_public=True))
        db.commit()
    return svc


@pytest.fixture
def casts(block, monkeypatch):
    sent = []
    monkeypatch.setattr(block.clients, "cast_to_worker", lambda *a, **kw: sent.append(a))
    return sent


def test_each_gated_route_has_its_action(block):
    found = {key: action for key, action in route_actions(block.app).items() if action.startswith("block-store:")}

    assert found == EXPECTED
    assert len(found) == 6
    # the two sub-actions are decided inside the handler, so no route carries them
    assert "block-store:volume:force-delete" not in found.values()
    assert "block-store:volume:reset-status" not in found.values()


def test_member_domain_admin_and_reader_are_refused_and_admin_is_not(block):
    assert_gated(block.app, block.who, ROUTES)


def test_admin_runs_the_gated_routes_without_a_server_error(block):
    client = TestClient(block.app, raise_server_exceptions=False)
    calls = [
        ("POST", "/volume-types", {"name": "silver", "backend": "lvm", "disk_type": "sata_hdd"}, 201),
        ("PATCH", "/volume-types/gold", {"description": "x"}, 200),
        ("DELETE", "/volume-types/silver", None, 200),
        ("PUT", "/os-quota-sets/p1", {"volumes": 5}, 200),
        ("DELETE", "/os-quota-sets/p1", None, 204),
        ("POST", "/volumes/own/force-detach", None, 200),
    ]
    for method, path, body, expected in calls:
        r = client.request(method, path, json=body)
        assert r.status_code == expected, (method, path, r.status_code, r.text)


def test_force_detach_still_accepts_a_positional_admin_call(block):
    with block.SessionLocal() as db:
        forced = block.force_detach_volume("own", {"role": "admin"}, db)

    assert forced["status"] == "available"


@pytest.mark.parametrize("body,action", SUB_ACTIONS)
def test_force_delete_and_reset_status_refuse_a_member_on_their_own_volume_with_the_policy_text(
    block, casts, body, action
):
    block.who.update(role="member")
    client = TestClient(block.app, raise_server_exceptions=False)

    r = client.post("/volumes/own/action", json=body)

    assert r.status_code == 403
    assert r.json() == {"detail": f"Policy does not allow {action}"}
    assert casts == []
    with block.SessionLocal() as db:
        assert db.get(block.Volume, "v-own").status == "available"


@pytest.mark.parametrize("body,action", SUB_ACTIONS)
def test_a_member_on_another_projects_volume_gets_404_not_403(block, casts, body, action):
    block.who.update(role="member")
    client = TestClient(block.app, raise_server_exceptions=False)

    r = client.post("/volumes/other/action", json=body)

    assert r.status_code == 404, r.text


def test_admin_reaches_both_sub_actions(block, casts):
    client = TestClient(block.app, raise_server_exceptions=False)

    r = client.post("/volumes/own/action", json={"os-reset_status": {"status": "error"}})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "error"

    r = client.post("/volumes/own/action", json={"os-force_delete": {}})
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "deleting", "id": "v-own"}
    assert len(casts) == 1


def test_a_member_can_still_use_extend_and_migrate_sub_actions(block, casts):
    block.who.update(role="member")
    client = TestClient(block.app, raise_server_exceptions=False)

    r = client.post("/volumes/own/action", json={"os-extend_volume": {"new_size": 5}})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "extending"

    with block.SessionLocal() as db:
        db.get(block.Volume, "v-own").status = "available"
        db.commit()
    r = client.post("/volumes/own/action", json={"os-migrate_volume": {"host": "node2"}})
    assert r.status_code == 200, r.text
    assert r.json()["migration_status"] == "migrating"


def test_a_policy_file_can_open_force_delete_to_members(block, casts, pfile):
    block.who.update(role="member")
    client = TestClient(block.app, raise_server_exceptions=False)
    assert client.post("/volumes/own/action", json={"os-force_delete": {}}).status_code == 403

    pfile.write('"block-store:volume:force-delete": "role:member or role:admin"\n')

    r = client.post("/volumes/own/action", json={"os-force_delete": {}})
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "deleting", "id": "v-own"}
    # the sibling sub-action keeps its own rule
    assert client.post("/volumes/own/action", json={"os-reset_status": {}}).status_code == 403


def test_a_policy_file_can_close_a_route_for_admins(block, pfile):
    client = TestClient(block.app, raise_server_exceptions=False)
    assert client.delete("/os-quota-sets/p1").status_code == 204

    pfile.write('"block-store:quota:clear": "!"\n')

    r = client.delete("/os-quota-sets/p1")
    assert r.status_code == 403
    assert r.json() == {"detail": "Policy does not allow block-store:quota:clear"}


def test_list_volume_types_still_runs_for_a_member(block):
    block.who.update(role="member")
    client = TestClient(block.app, raise_server_exceptions=False)

    r = client.get("/volume-types")

    assert r.status_code == 200
    assert [item["name"] for item in r.json()["items"]] == ["gold"]
