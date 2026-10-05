"""Project quota enforcement when creating servers (auth-api holds the limits, placement the usage)."""
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


@pytest.fixture
def check(monkeypatch, tmp_path):
    compute = fresh_service("compute", monkeypatch, tmp_path)
    monkeypatch.setattr(compute.service, "_svc_headers", lambda: {})

    def run(quota, usages, vcpus=2, ram_mb=1024):
        def fake_get(url, **kwargs):
            return Resp(quota) if "/quota" in url else Resp({"usages": usages})

        monkeypatch.setattr(compute.service.requests, "get", fake_get)
        return compute.service._check_project_quota("proj-1", vcpus, ram_mb)

    return run


LIMITS = {"max_instances": 3, "max_vcpus": 8, "max_ram_mb": 4096}


def test_within_limits_passes(check):
    check(LIMITS, {"instances": 1, "VCPU": 2, "MEMORY_MB": 1024})


@pytest.mark.parametrize("usages, which", [
    ({"instances": 3, "VCPU": 0, "MEMORY_MB": 0}, "max_instances"),
    ({"instances": 0, "VCPU": 7, "MEMORY_MB": 0}, "max_vcpus"),
    ({"instances": 0, "VCPU": 0, "MEMORY_MB": 3500}, "max_ram_mb"),
])
def test_each_limit_is_enforced_with_413(check, usages, which):
    with pytest.raises(HTTPException) as exc:
        check(LIMITS, usages)
    assert exc.value.status_code == 413 and which in exc.value.detail


def test_reaching_exactly_the_limit_is_allowed(check):
    check(LIMITS, {"instances": 2, "VCPU": 6, "MEMORY_MB": 3072})  # 3 instances, 8 vCPUs, 4096 MB after this one


def test_defaults_apply_when_auth_returns_no_limits(check):
    with pytest.raises(HTTPException):
        check({}, {"instances": 10})  # default max_instances is 10


def test_unreachable_auth_api_skips_the_check_and_says_so(monkeypatch, tmp_path, caplog):
    compute = fresh_service("compute", monkeypatch, tmp_path)
    monkeypatch.setattr(compute.service, "_svc_headers", lambda: {})

    def down(*a, **k):
        raise requests.ConnectionError("auth-api down")

    monkeypatch.setattr(compute.service.requests, "get", down)
    with caplog.at_level(logging.WARNING):
        compute.service._check_project_quota("proj-1", 64, 10**6)  # would blow every limit, but cannot be checked
    assert "Quota check failed/skipped" in caplog.text
