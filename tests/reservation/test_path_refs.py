import importlib
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from shared.auth_middleware import require_auth


@pytest.fixture
def rv(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'rv.sqlite'}")
    import services.reservation.app.main as m
    m = importlib.reload(m)
    m.Base.metadata.create_all(bind=m.engine)
    m.cluster_capacity = lambda request: (8, 8192)
    m.sent = []

    def call(request, base, path):
        m.sent.append(path)
        return {"name": "small", "vcpus": 2, "ram_mb": 2048}
    m._call = call
    m.app.dependency_overrides[require_auth] = lambda: {"project_id": "p1", "role": "member"}
    return TestClient(m.app), m


def lease(c, flavor):
    start = datetime.utcnow() + timedelta(hours=1)
    return c.post("/leases", json={"name": "a", "flavor": flavor, "amount": 1, "start_at": start.isoformat(),
                                   "end_at": (start + timedelta(hours=1)).isoformat()})


@pytest.mark.parametrize("flavor", ["..", ".", ""])
def test_flavor_that_names_no_resource_is_refused_before_the_request(rv, flavor):
    c, m = rv
    r = lease(c, flavor)
    assert r.status_code == 404 and r.json()["detail"] == f"'{flavor}' not found"
    assert m.sent == []


@pytest.mark.parametrize("flavor,sent", [("a/b", "/flavors/a%2Fb"), ("a?b", "/flavors/a%3Fb")])
def test_flavor_is_one_path_segment(rv, flavor, sent):
    c, m = rv
    assert lease(c, flavor).status_code == 201
    assert m.sent == [sent]


def test_plain_flavor_is_sent_as_is(rv):
    c, m = rv
    assert lease(c, "m1.small").status_code == 201
    assert m.sent == ["/flavors/m1.small"]
