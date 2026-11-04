"""The admin-only host reset of ha is gated by the central policy; the other routes are not."""
import importlib

import pytest
from fastapi.testclient import TestClient

from shared.auth_middleware import require_auth
from tests.gates import assert_gated, route_actions

EXPECTED = {("POST", "/hosts/{host}/reset"): "ha:host:reset"}
ROUTES = [(method, path, action) for (method, path), action in EXPECTED.items()]
HB = {"X-Internal-Token": "tok"}


@pytest.fixture
def ha(monkeypatch, tmp_path, pfile):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'ha.sqlite'}")
    monkeypatch.setenv("INTERNAL_TOKEN", "tok")
    import services.ha.app.main as m
    m = importlib.reload(m)
    m.Base.metadata.create_all(bind=m.engine)
    monkeypatch.setattr(m, "start_monitor", lambda: None)
    who = {"sub": "u", "project_id": "p1", "role": "admin", "domain_admin": False}
    m.app.dependency_overrides[require_auth] = lambda: who
    c = TestClient(m.app, raise_server_exceptions=False)
    c.who, c.m = who, m
    return c


def test_the_gated_route_has_its_action(ha):
    found = {key: action for key, action in route_actions(ha.m.app).items() if action.startswith("ha:")}

    assert found == EXPECTED


def test_member_domain_admin_and_reader_are_refused_and_admin_is_not(ha):
    assert_gated(ha.m.app, ha.who, ROUTES)


def test_admin_resets_a_fenced_host_without_a_server_error(ha):
    ha.post("/internal/heartbeat/h1", json={}, headers=HB)
    with ha.m.SessionLocal() as db:
        db.get(ha.m.Host, "h1").state = "fenced"
        db.commit()

    r = ha.post("/hosts/h1/reset")

    assert r.status_code == 200, r.text
    assert r.json() == {"host": "h1", "state": "up"}


def test_a_member_still_lists_hosts(ha):
    ha.who["role"] = "member"

    assert ha.get("/hosts").status_code == 200


def test_a_policy_file_can_open_host_reset_to_members(ha, pfile):
    ha.post("/internal/heartbeat/h1", json={}, headers=HB)
    ha.who["role"] = "member"
    assert ha.post("/hosts/h1/reset").status_code == 403

    pfile.write('"ha:host:reset": "role:member"\n')

    assert ha.post("/hosts/h1/reset").status_code == 200
