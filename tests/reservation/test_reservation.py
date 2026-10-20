import importlib
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from shared.auth_middleware import require_auth

H = timedelta(hours=1)
T0 = datetime(2030, 1, 1)

def test_peak_counts_only_overlap_at_one_moment():
    from services.reservation.app.main import peak
    ls = [(T0, T0 + 2 * H, 4, 40), (T0 + H, T0 + 3 * H, 2, 20), (T0 + 3 * H, T0 + 4 * H, 8, 80)]
    assert peak(ls, T0, T0 + 4 * H) == (8, 80)                    # the 8 vCPU one runs alone
    assert peak(ls, T0 + H, T0 + 2 * H) == (6, 60)                # first two overlap
    assert peak(ls, T0 + 2 * H, T0 + 3 * H) == (2, 20)
    assert peak(ls, T0 + 4 * H, T0 + 5 * H) == (0, 0)             # end is exclusive
    assert peak([], T0, T0 + H) == (0, 0)

@pytest.fixture
def rv(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'rv.sqlite'}")
    import services.reservation.app.main as m
    m = importlib.reload(m)
    m.Base.metadata.create_all(bind=m.engine)
    m.cluster_capacity = lambda request: (8, 8192)
    m._call = lambda request, base, path: {"name": "small", "vcpus": 2, "ram_mb": 2048}
    who = {"project_id": "p1", "role": "member"}
    m.app.dependency_overrides[require_auth] = lambda: who
    c = TestClient(m.app)
    c.who = who
    return c

def lease(c, name, start, hours=1, amount=1):
    s = datetime.utcnow() + timedelta(hours=start)
    return c.post("/leases", json={"name": name, "flavor": "small", "amount": amount, "start_at": s.isoformat(), "end_at": (s + hours * H).isoformat()})

def test_admission_and_status(rv):
    r = lease(rv, "a", 1, amount=3)
    assert r.status_code == 201, r.text
    assert r.json()["vcpus"] == 6 and r.json()["status"] == "pending"
    assert lease(rv, "a", 5).status_code == 409                   # name taken
    assert lease(rv, "b", 1, amount=2).status_code == 409         # 6 + 4 > 8 vCPU in the same hour
    assert lease(rv, "c", 3, amount=4).status_code == 201         # a different hour is free
    rv.who["project_id"] = "p2"                                   # capacity is cluster wide
    assert lease(rv, "d", 1, amount=2).status_code == 409
    assert rv.get("/leases").json()["items"] == []                # but p2 cannot see p1's leases
    rv.who["project_id"] = "p1"
    free = rv.get("/capacity", params={"start": (datetime.utcnow() + 1.2 * H).isoformat(), "end": (datetime.utcnow() + 1.5 * H).isoformat()}).json()["free"]
    assert free == {"vcpus": 2, "ram_mb": 8192 - 6144}
    assert rv.delete("/leases/a").status_code == 204
    assert lease(rv, "b", 1, amount=2).status_code == 201

def test_validation(rv):
    assert lease(rv, "x", -5).status_code == 422                  # past
    assert lease(rv, "x", 1, hours=24 * 31).status_code == 422
    assert lease(rv, "x", 1, hours=0).status_code == 422
    assert lease(rv, "x", 1, amount=0).status_code == 422
    assert lease(rv, "bad name", 1).status_code == 422
    assert rv.get("/leases/nope").status_code == 404
