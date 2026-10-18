"""Project quota enforcement when creating servers (auth-api holds the limits, compute's servers table the usage)."""
import logging

import pytest
import requests
from fastapi import HTTPException

from tests.stack import fresh_service

class Resp:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass

@pytest.fixture
def check(monkeypatch, tmp_path):
    compute = fresh_service("compute", monkeypatch, tmp_path)
    monkeypatch.setattr(compute.service, "internal_headers", lambda: {})
    db = compute.SessionLocal()

    def run(quota, servers=(), vcpus=2, ram_mb=1024):
        """servers: (project, status, vcpus, ram_mb) rows already in the DB."""
        for i, (proj, status, v, r) in enumerate(servers):
            db.add(compute.Server(id=f"id{i}", name=f"s{i}", image_id="i", network_id="n", port_id="p", flavor="tiny",
                                  status=status, backend="libvirt", project_id=proj, vcpus=v, ram_mb=r))
        db.commit()
        monkeypatch.setattr(compute.service.requests, "get", lambda url, **kw: Resp(quota))
        return compute.service._check_project_quota(db, "proj-1", vcpus, ram_mb)

    yield run
    db.close()

LIMITS = {"max_instances": 3, "max_vcpus": 8, "max_ram_mb": 4096}
ACTIVE = lambda v=2, r=1024: ("proj-1", "ACTIVE", v, r)  # noqa: E731

def test_within_limits_passes(check):
    check(LIMITS, [ACTIVE()])

@pytest.mark.parametrize("servers, which", [
    ([ACTIVE(0, 0)] * 3, "max_instances"),
    ([ACTIVE(7, 0)], "max_vcpus"),
    ([ACTIVE(0, 3500)], "max_ram_mb"),
])
def test_each_limit_is_enforced_with_413(check, servers, which):
    with pytest.raises(HTTPException) as exc:
        check(LIMITS, servers)
    assert exc.value.status_code == 413 and which in exc.value.detail

def test_reaching_exactly_the_limit_is_allowed(check):
    check(LIMITS, [ACTIVE(3, 1024), ACTIVE(3, 2048)])  # 3 instances, 8 vCPUs, 4096 MB after this one

def test_deleted_and_other_projects_servers_do_not_count(check):
    check({"max_instances": 1}, [("proj-1", "DELETED", 2, 1024), ("proj-2", "ACTIVE", 2, 1024)])

def test_defaults_apply_when_auth_returns_no_limits(check):
    with pytest.raises(HTTPException):
        check({}, [ACTIVE(0, 0)] * 10)  # default max_instances is 10

def test_unreachable_auth_api_skips_the_check_and_says_so(monkeypatch, tmp_path, caplog):
    compute = fresh_service("compute", monkeypatch, tmp_path)
    monkeypatch.setattr(compute.service, "internal_headers", lambda: {})

    def down(*a, **k):
        raise requests.ConnectionError("auth-api down")

    monkeypatch.setattr(compute.service.requests, "get", down)
    with caplog.at_level(logging.WARNING):
        compute.service._check_project_quota(None, "proj-1", 64, 10**6)  # would blow every limit, but cannot be checked
    assert "Quota check skipped" in caplog.text

def test_quota_is_rechecked_right_before_the_insert():
    """The domain advisory lock is only released by the commit that inserts the server, so the (second) check
    must sit between the Server(...) build and db.add(server), not only at the top of create_server."""
    import inspect
    import re

    from services.compute.app.routers import servers
    src = inspect.getsource(servers.create_server)
    assert len(re.findall(r"_check_project_quota\(", src)) == 2
    assert src.rindex("_check_project_quota(") < src.index("db.add(server)")
    assert src.index("server = Server(") < src.rindex("_check_project_quota(")