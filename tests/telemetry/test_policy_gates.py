"""The 2 admin-only routes of telemetry are gated by the central policy; the bulk routes are not."""
import importlib
import os

import pytest
from fastapi.testclient import TestClient

from shared import policy
from shared.auth_middleware import require_auth
from tests.gates import assert_gated, route_actions

EXPECTED = {
    ("POST", "/telemetry/metrics"): "telemetry:metric:create",
    ("POST", "/telemetry/alarms/evaluate"): "telemetry:alarm:evaluate",
}
ROUTES = [(method, path, action) for (method, path), action in EXPECTED.items()]
METRIC = {"instance_id": "i1", "project_id": "p1", "metric_type": "cpu_percent", "value": 1.0}
BATCH = [METRIC]
INTERNAL = {"X-Internal-Token": "service-token"}


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
def telemetry(monkeypatch, tmp_path, pfile):
    monkeypatch.setenv("REVOCATION_CHECK", "false")
    monkeypatch.setenv("INTERNAL_TOKEN", "service-token")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    import services.telemetry.app.main as m

    m = importlib.reload(m)
    m.Base.metadata.create_all(m.engine)
    m.client = TestClient(m.app, raise_server_exceptions=False)
    who = {"sub": "u", "role": "admin", "project_id": "p1", "domain_admin": False}
    m.app.dependency_overrides[require_auth] = lambda: who
    m.who = who
    yield m
    m.app.dependency_overrides.pop(require_auth, None)


def test_each_gated_route_has_its_action(telemetry):
    found = {key: action for key, action in route_actions(telemetry.app).items() if action.startswith("telemetry:")}

    assert found == EXPECTED
    assert not [path for (_, path) in found if path.endswith(("/batch", "/cleanup"))]


def test_member_domain_admin_and_reader_are_refused_and_admin_is_not(telemetry):
    assert_gated(telemetry.app, telemetry.who, ROUTES)


def test_admin_runs_the_gated_routes_without_a_server_error(telemetry):
    created = telemetry.client.post("/telemetry/metrics", json=METRIC)
    assert created.status_code == 201, created.text
    evaluated = telemetry.client.post("/telemetry/alarms/evaluate")
    assert evaluated.status_code == 200, evaluated.text
    assert evaluated.json() == {"changed": 0}


def test_a_policy_file_can_close_alarm_evaluate_for_admins(telemetry, pfile):
    pfile.write('"telemetry:alarm:evaluate": "!"\n')

    r = telemetry.client.post("/telemetry/alarms/evaluate")

    assert r.status_code == 403
    assert r.json() == {"detail": "Policy does not allow telemetry:alarm:evaluate"}


def test_the_bulk_routes_are_not_governed_by_the_file(telemetry, pfile):
    pfile.write('"telemetry:metric:create": "!"\n')

    closed = telemetry.client.post("/telemetry/metrics", json=METRIC)
    assert closed.status_code == 403

    telemetry.app.dependency_overrides.pop(require_auth)
    r = telemetry.client.post("/telemetry/metrics/batch", json=BATCH, headers=INTERNAL)
    assert r.status_code == 201, r.text
