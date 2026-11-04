"""The 12 admin-only routes of compute are gated by the central policy (shared.policy)."""
import os
import re
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "services"))

from shared import policy  # noqa: E402
from shared.auth_middleware import require_auth  # noqa: E402
from tests.gates import assert_gated, route_actions  # noqa: E402
from tests.stack import fresh_service  # noqa: E402

EXPECTED = {
    ("POST", "/flavors"): "compute:flavor:create",
    ("DELETE", "/flavors/{ref}"): "compute:flavor:delete",
    ("POST", "/flavors/{ref}/os-extra_specs"): "compute:flavor-extra-spec:set",
    ("DELETE", "/flavors/{ref}/os-extra_specs/{key}"): "compute:flavor-extra-spec:delete",
    ("POST", "/os-aggregates"): "compute:aggregate:create",
    ("PUT", "/os-aggregates/{agg_ref}"): "compute:aggregate:update",
    ("DELETE", "/os-aggregates/{agg_ref}"): "compute:aggregate:delete",
    ("POST", "/os-aggregates/{agg_ref}/action"): "compute:aggregate:action",
    ("PUT", "/os-quota-sets/{project}"): "compute:quota:set",
    ("PUT", "/os-services/{host}"): "compute:service:update",
    ("POST", "/servers/{ref}/migrate"): "compute:server:migrate",
    ("POST", "/servers/{ref}/evacuate"): "compute:server:evacuate",
}
ROUTES = [(method, path, action) for (method, path), action in EXPECTED.items()]


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
def compute(monkeypatch, tmp_path, pfile):
    svc = fresh_service("compute", monkeypatch, tmp_path)
    who = {"sub": "u", "role": "admin", "project_id": "p1", "domain_admin": False}
    svc.app.dependency_overrides[require_auth] = lambda: who
    svc.who = who
    return svc


def _fill(template):
    return re.sub(r"\{[^}]+\}", "x", template)


def test_each_gated_route_has_its_action(compute):
    found = {key: action for key, action in route_actions(compute.app).items() if action.startswith("compute:")}

    assert found == EXPECTED
    assert len(found) == 12


def test_member_domain_admin_and_reader_are_refused_and_admin_is_not(compute):
    assert_gated(compute.app, compute.who, ROUTES)


def test_admin_runs_every_gated_route_without_a_server_error(compute, monkeypatch):
    class R:
        status_code = 200
        text = ""

        def json(self):
            return {"project_id": "x"}

    monkeypatch.setattr(compute.ops.requests, "request", lambda *a, **kw: R())
    client = TestClient(compute.app, raise_server_exceptions=False)

    for method, template, action in ROUTES:
        r = client.request(method, _fill(template), json={})
        assert r.status_code < 500, (method, template, r.status_code, r.text)
        assert r.status_code not in (401, 403), (method, template, r.status_code, r.text)


@pytest.mark.parametrize("method,path", [("PUT", "/os-services/n1"), ("POST", "/servers/x/evacuate")])
def test_internal_token_passes_update_service_and_evacuate_without_any_override(compute, monkeypatch, method, path):
    compute.app.dependency_overrides.clear()
    monkeypatch.setenv("INTERNAL_TOKEN", "tok")
    client = TestClient(compute.app, raise_server_exceptions=False)

    r = client.request(method, path, json={}, headers={"X-Internal-Token": "tok"})

    assert r.status_code not in (401, 403), r.text
    assert r.status_code < 500, r.text


@pytest.mark.parametrize("method,path", [("PUT", "/os-services/n1"), ("POST", "/servers/x/evacuate")])
def test_a_wrong_internal_token_is_refused(compute, monkeypatch, method, path):
    compute.app.dependency_overrides.clear()
    monkeypatch.setenv("INTERNAL_TOKEN", "tok")
    client = TestClient(compute.app, raise_server_exceptions=False)

    r = client.request(method, path, json={}, headers={"X-Internal-Token": "nope"})

    assert r.status_code in (401, 403), r.text


def test_a_policy_file_can_open_flavor_create_to_members(compute, pfile):
    compute.who.update(role="member")
    client = TestClient(compute.app, raise_server_exceptions=False)
    assert client.post("/flavors", json={}).status_code == 403

    pfile.write('"compute:flavor:create": "role:member or role:admin"\n')

    r = client.post("/flavors", json={})
    assert r.status_code == 422, r.text  # past the gate, stopped by body validation


def test_a_policy_file_can_close_flavor_create_for_admins(compute, pfile):
    client = TestClient(compute.app, raise_server_exceptions=False)
    assert client.post("/flavors", json={}).status_code == 422

    pfile.write('"compute:flavor:create": "!"\n')

    r = client.post("/flavors", json={})
    assert r.status_code == 403
    assert r.json() == {"detail": "Policy does not allow compute:flavor:create"}
