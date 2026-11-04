"""The 2 admin-only bucket-type routes of object-store are gated by the central policy; the reads are not."""
import importlib
import os

import pytest
from fastapi.testclient import TestClient

from shared import policy
from shared.auth_middleware import require_auth
from tests.gates import assert_gated, route_actions

EXPECTED = {
    ("POST", "/admin/bucket-types"): "object-store:bucket-type:create",
    ("DELETE", "/admin/bucket-types/{ref}"): "object-store:bucket-type:delete",
}
ROUTES = [(method, path, action) for (method, path), action in EXPECTED.items()]
BUCKET_TYPE = {"name": "fs", "driver": "filesystem", "config": {"root": "/tmp/unused"}}


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
def store(monkeypatch, tmp_path, pfile):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'object-store.sqlite'}")
    import services.object_store.app.main as m
    m = importlib.reload(m)
    m.Base.metadata.create_all(bind=m.engine)
    m.who = {"sub": "u", "role": "admin", "project_id": "p1", "domain_admin": False}
    m.app.dependency_overrides[require_auth] = lambda: m.who
    m.client = TestClient(m.app, raise_server_exceptions=False)
    return m


def test_each_gated_route_has_its_action(store):
    found = {key: action for key, action in route_actions(store.app).items() if action.startswith("object-store:")}

    assert found == EXPECTED


def test_member_domain_admin_and_reader_are_refused_and_admin_is_not(store):
    assert_gated(store.app, store.who, ROUTES)


def test_admin_creates_and_deletes_a_bucket_type_without_a_server_error(store):
    created = store.client.post("/admin/bucket-types", json=BUCKET_TYPE)
    assert created.status_code == 201, created.text

    assert store.client.delete(f"/admin/bucket-types/{created.json()['id']}").status_code == 204


def test_a_member_still_reads_bucket_types(store):
    store.client.post("/admin/bucket-types", json=BUCKET_TYPE)
    store.who["role"] = "member"

    listed = store.client.get("/admin/bucket-types")
    assert listed.status_code == 200
    assert [bt["config"] for bt in listed.json()["bucket_types"]] == [{}]  # the config stays admin-only


def test_a_policy_file_can_close_bucket_type_create_for_admins(store, pfile):
    assert store.client.post("/admin/bucket-types", json=BUCKET_TYPE).status_code == 201

    pfile.write('"object-store:bucket-type:create": "!"\n')

    r = store.client.post("/admin/bucket-types", json={**BUCKET_TYPE, "name": "fs2"})
    assert r.status_code == 403
    assert r.json() == {"detail": "Policy does not allow object-store:bucket-type:create"}
