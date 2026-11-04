"""The 2 admin-only delete routes of networking are gated by the central policy; create stays open to members."""
import os

import pytest
from fastapi.testclient import TestClient

from shared import policy
from shared.auth_middleware import require_auth
from tests.gates import assert_gated, route_actions

EXPECTED = {
    ("DELETE", "/qos/policies/{policy_id}"): "networking:qos-policy:delete",
    ("DELETE", "/subnetpools/{pool_id}"): "networking:subnet-pool:delete",
}
ROUTES = [(method, path, action) for (method, path), action in EXPECTED.items()]
POOL = {"name": "pool1", "prefixes": ["10.50.0.0/16"]}


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
def api(networking, pfile):
    """require_auth answers `who`, a dict the test may change."""
    who = {"sub": "u", "role": "admin", "project_id": "p1", "domain_admin": False}
    networking.who = who
    networking.app.dependency_overrides[require_auth] = lambda: who
    networking.client = TestClient(networking.app, raise_server_exceptions=False)
    return networking


def test_each_gated_route_has_its_action(api):
    found = {key: action for key, action in route_actions(api.app).items() if action.startswith("networking:")}

    assert found == EXPECTED


def test_member_domain_admin_and_reader_are_refused_and_admin_is_not(api):
    assert_gated(api.app, api.who, ROUTES)


def test_admin_deletes_a_qos_policy_and_a_subnet_pool_without_a_server_error(api):
    qos = api.client.post("/qos/policies", json={"name": "q1"})
    assert qos.status_code == 201, qos.text
    pool = api.client.post("/subnetpools", json=POOL)
    assert pool.status_code == 201, pool.text

    assert api.client.delete(f"/qos/policies/{qos.json()['id']}").status_code == 204
    assert api.client.delete(f"/subnetpools/{pool.json()['id']}").status_code == 204


def test_a_member_can_still_create_a_qos_policy_but_not_delete_one(api):
    api.who["role"] = "member"

    created = api.client.post("/qos/policies", json={"name": "q1"})
    assert created.status_code == 201, created.text

    refused = api.client.delete(f"/qos/policies/{created.json()['id']}")
    assert refused.status_code == 403
    assert refused.json() == {"detail": "Policy does not allow networking:qos-policy:delete"}


def test_a_member_can_still_create_a_subnet_pool_but_not_delete_one(api):
    api.who["role"] = "member"

    created = api.client.post("/subnetpools", json=POOL)
    assert created.status_code == 201, created.text

    refused = api.client.delete(f"/subnetpools/{created.json()['id']}")
    assert refused.status_code == 403
    assert refused.json() == {"detail": "Policy does not allow networking:subnet-pool:delete"}


def test_a_policy_file_can_open_qos_policy_delete_to_members(api, pfile):
    api.who["role"] = "member"
    created = api.client.post("/qos/policies", json={"name": "q1"})
    assert api.client.delete(f"/qos/policies/{created.json()['id']}").status_code == 403

    pfile.write('"networking:qos-policy:delete": "role:member"\n')

    assert api.client.delete(f"/qos/policies/{created.json()['id']}").status_code == 204
