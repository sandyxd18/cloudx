from datetime import datetime, timezone
from decimal import Decimal

import pytest

S = datetime(2026, 10, 1, 10, tzinfo=timezone.utc)
E = datetime(2026, 10, 1, 11, tzinfo=timezone.utc)


@pytest.fixture
def w():
    from services.billing_worker.app import main
    return main


def test_overlap_hours(w):
    h = w.overlap_hours
    assert h(None, None, S, E) == 1  # unknown lifetime: whole period
    assert h("2026-10-01T10:30:00", None, S, E) == Decimal("0.5")  # created mid-period (naive = UTC)
    assert h("2026-10-01T09:00:00Z", "2026-10-01T10:15:00Z", S, E) == Decimal("0.25")  # deleted mid-period
    assert h("2026-10-01T10:20:00", "2026-10-01T10:40:00", S, E) == pytest.approx(Decimal("1") / 3)
    assert h("2026-10-01T11:00:00", None, S, E) == 0  # created at end
    assert h("2026-09-01T00:00:00", "2026-10-01T09:59:00", S, E) == 0  # deleted before


def test_aggregate_skips_no_overlap_and_uses_hour(w):
    res = [
        {"id": "a", "project_id": "p", "created_at": "2026-10-01T10:30:00"},
        {"id": "b", "project_id": "p", "created_at": "2026-10-01T12:00:00"},
        {"id": "c", "project_id": "p", "created_at": "2026-09-01T00:00:00", "deleted_at": "2026-10-01T09:00:00"},
    ]
    fips = [dict(r, port_id="x") for r in res]
    lbs = [dict(r, provisioning_status="ACTIVE") for r in res]
    for recs in (w._aggregate_floating_ip_usage(fips, S, E), w._aggregate_load_balancer_usage(lbs, S, E)):
        assert [r["resource_id"] for r in recs] == ["a"]
        assert recs[0]["unit"] == "hour" and Decimal(recs[0]["quantity"]) == Decimal("0.5")
        assert "rate_per_unit" not in recs[0]
    assert w._aggregate_load_balancer_usage(lbs, S, E)[0]["resource_type"] == "load_balancer"


def test_server_usage(w):
    srv = [{"id": "s", "project_id": "p", "status": "ACTIVE", "vcpus": 2, "ram_mb": 2048, "created_at": "2026-09-01T00:00:00"}]
    recs = {r["resource_type"]: Decimal(r["quantity"]) for r in w._aggregate_server_usage(srv, S, E)}
    assert recs == {"vcpu": 2, "ram": 2}


def test_token_refreshed_once_on_401(w, monkeypatch):
    class R:
        def __init__(self, code): self.status_code = code

    tokens = iter(["t1", "t2"])
    monkeypatch.setattr(w, "_svc_token", None)
    monkeypatch.setattr(w.requests, "post", lambda *a, **k: type("T", (), {"status_code": 200, "json": lambda s: {"token": next(tokens)}})())
    seen = []

    def fake(method, url, headers=None, **kw):
        seen.append(headers["Authorization"])
        return R(401 if len(seen) == 1 else 200)

    monkeypatch.setattr(w.requests, "request", fake)
    assert w._request("GET", "http://x").status_code == 200
    assert seen == ["Bearer t1", "Bearer t2"]

def test_ghosts_billed_until_mid_interval(w):
    state = {"servers": {"a": {"id": "a", "project_id": "p"}}}
    out = w._with_ghosts("servers", [{"id": "b"}], state, S, E)
    assert [r["id"] for r in out] == ["b", "a"] and out[1]["deleted_at"] == "2026-10-01T10:30:00+00:00"
    assert w.overlap_hours(None, out[1]["deleted_at"], S, E) == Decimal("0.5")
    assert list(state["servers"]) == ["b"]  # next run forgets "a" -> billed once
    assert w._with_ghosts("servers", None, state, S, E) == [] and list(state["servers"]) == ["b"]  # API error keeps snapshot


def test_load_balancer_per_type_rates(w):
    lbs = [
        {"id": "lb1", "project_id": "p", "provisioning_status": "ACTIVE", "lb_type": "NLB", "created_at": "2026-10-01T10:00:00"},
        {"id": "lb2", "project_id": "p", "provisioning_status": "ACTIVE", "lb_type": "ALB", "created_at": "2026-10-01T10:00:00"},
        {"id": "lb3", "project_id": "p", "provisioning_status": "ACTIVE", "created_at": "2026-10-01T10:00:00"},
    ]
    recs = w._aggregate_load_balancer_usage(lbs, S, E)
    assert len(recs) == 3
    assert recs[0]["resource_type"] == "load_balancer_nlb"
    assert recs[1]["resource_type"] == "load_balancer_alb"
    assert recs[2]["resource_type"] == "load_balancer"
