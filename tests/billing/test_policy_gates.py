"""The 3 admin-only rating/usage routes of billing are gated by the central policy; the bulk routes are not."""
import importlib
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from jose import jwt

from shared import policy
from shared.auth_middleware import require_auth
from tests.gates import assert_gated, route_actions

EXPECTED = {
    ("POST", "/billing/rating-sheets"): "billing:rating-sheet:create",
    ("PATCH", "/billing/rating-sheets/{sheet_id}"): "billing:rating-sheet:update",
    ("POST", "/billing/usage-records"): "billing:usage-record:create",
}
ROUTES = [(method, path, action) for (method, path), action in EXPECTED.items()]
SHEET = {"resource_type": "instance", "unit": "hour", "rate_per_unit": "0.5"}
RECORD = {"project_id": "p1", "resource_type": "instance", "quantity": "1", "unit": "hour", "rate_per_unit": "2",
          "period_start": "2026-01-01T00:00:00Z", "period_end": "2026-01-02T00:00:00Z"}
BATCH = [{"project_id": "p1", "resource_type": "instance", "quantity": "1",
          "period_start": "2026-01-01T00:00:00Z", "period_end": "2026-01-02T00:00:00Z"}]
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
def billing(monkeypatch, tmp_path, pfile):
    monkeypatch.setenv("REVOCATION_CHECK", "false")
    monkeypatch.setenv("INTERNAL_TOKEN", "service-token")
    monkeypatch.setenv("BILLING_DATABASE_URL", f"sqlite:///{tmp_path / 'billing.sqlite'}")
    sys.modules.pop("services.billing.app.main", None)
    b = importlib.import_module("services.billing.app.main")
    b.Base.metadata.create_all(b.engine)
    with b.engine.begin() as conn:  # stands in for cloudx-db-migrate
        conn.exec_driver_sql("CREATE TABLE alembic_version_billing (version_num VARCHAR(32))")
    b.client = TestClient(b.app, raise_server_exceptions=False)
    return b


@pytest.fixture
def as_who(billing):
    """require_auth answers `who`, a dict the test may change."""
    who = {"sub": "u", "role": "admin", "project_id": "p1", "domain_admin": False}
    billing.app.dependency_overrides[require_auth] = lambda: who
    yield who
    billing.app.dependency_overrides.pop(require_auth, None)


def token(role="member", domain_admin=False, scope_type="project"):
    claims = {"sub": "u", "username": "u", "project_id": "p1", "role": role, "roles": [role],
              "scope_type": scope_type, "domain_id": "d1", "domain_admin": domain_admin,
              "exp": datetime.now(timezone.utc) + timedelta(hours=1)}
    from shared import auth_middleware as am
    return {"X-Auth-Token": jwt.encode(claims, am.SECRET_KEY, algorithm=am.ALGORITHM)}


def test_each_gated_route_has_its_action(billing):
    found = {key: action for key, action in route_actions(billing.app).items() if action.startswith("billing:")}

    assert found == EXPECTED
    assert not [path for (_, path) in found if path.endswith(("/batch", "/cleanup"))]


def test_member_domain_admin_and_reader_are_refused_and_admin_is_not(billing, as_who):
    assert_gated(billing.app, as_who, ROUTES)


def test_admin_runs_the_gated_routes_without_a_server_error(billing, as_who):
    created = billing.client.post("/billing/rating-sheets", json=SHEET)
    assert created.status_code == 201, created.text
    patched = billing.client.patch(f"/billing/rating-sheets/{created.json()['id']}", json={"rate_per_unit": "0.75"})
    assert patched.status_code == 200, patched.text
    record = billing.client.post("/billing/usage-records", json=RECORD)
    assert record.status_code == 201, record.text


def test_a_domain_scoped_admin_flavoured_token_is_refused_with_the_policy_text(billing):
    assert os.environ["ACCEPT_DOMAIN_SCOPE"] == "true"  # the token reaches the gate instead of being stopped by require_auth
    who = token(role="member", domain_admin=True, scope_type="domain")

    r = billing.client.post("/billing/rating-sheets", json=SHEET, headers=who)

    assert r.status_code == 403
    assert r.json() == {"detail": "Policy does not allow billing:rating-sheet:create"}


def test_a_policy_file_can_close_usage_record_create_for_admins(billing, pfile):
    admin = token(role="admin")
    assert billing.client.post("/billing/usage-records", json=RECORD, headers=admin).status_code == 201

    pfile.write('"billing:usage-record:create": "!"\n')

    r = billing.client.post("/billing/usage-records", json=RECORD, headers=admin)
    assert r.status_code == 403
    assert r.json() == {"detail": "Policy does not allow billing:usage-record:create"}


def test_the_bulk_routes_are_not_governed_by_the_file(billing, pfile):
    pfile.write('"billing:usage-record:create": "!"\n')

    closed = billing.client.post("/billing/usage-records", json=RECORD, headers=token(role="admin"))
    assert closed.status_code == 403

    r = billing.client.post("/billing/usage-records/batch", json=BATCH, headers=INTERNAL)
    assert r.status_code == 200, r.text
    assert set(r.json()) == {"created", "skipped"}
