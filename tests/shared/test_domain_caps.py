import pytest
from fastapi import HTTPException

from shared import domain


def test_check_domain_caps(monkeypatch):
    locked = []
    monkeypatch.setattr(domain, "lock_domain", lambda db, d: locked.append(d))
    dom = {"domain_id": "d1", "project_ids": ["p1", "p2"], "caps": {"max_instances": 3}}
    used = lambda ids: len(ids) + 1  # noqa: E731  (3 with two projects)

    domain.check_domain_caps(None, None, {"max_instances": (used, 1)})            # no domain block: nothing to do
    domain.check_domain_caps(None, {**dom, "caps": {}}, {"max_instances": (used, 1)})  # no caps: no lock either
    assert locked == []
    domain.check_domain_caps(None, dom, {"max_vcpus": (used, 99)})                 # a key without a cap is ignored, no lock
    assert locked == []
    with pytest.raises(HTTPException) as e:                                       # 3 used + 1 > 3
        domain.check_domain_caps(None, dom, {"max_instances": (used, 1)})
    assert e.value.status_code == 413 and "max_instances (3)" in e.value.detail and locked == ["d1"]
    domain.check_domain_caps(None, {**dom, "caps": {"max_instances": 4}}, {"max_instances": (used, 1)})  # 3 + 1 <= 4