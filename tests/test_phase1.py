"""
Tests for Phase 1: Security & Base Stability
- Project Quota enforcement in auth-api, placement-api, compute-api, and block-store-api
- Automated Health Probes in load-balancer
"""

import importlib
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


def test_auth_project_quota_crud(monkeypatch, tmp_path):
    db_path = tmp_path / "auth_quota.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    import services.auth.app.main as auth_main

    auth_main = importlib.reload(auth_main)
    auth_main.Base.metadata.create_all(bind=auth_main.engine)

    auth_main.app.dependency_overrides[auth_main._require_token] = lambda: {"project_id": "proj-123", "role": "admin", "username": "admin"}
    auth_main.app.dependency_overrides[auth_main._require_admin] = lambda: {"project_id": "proj-123", "role": "admin", "username": "admin"}
    client = TestClient(auth_main.app)

    # Seed admin project and user
    with auth_main.SessionLocal() as db:
        p = auth_main.Project(id="proj-123", name="test-proj")
        db.add(p)
        db.commit()

    # GET default quota
    res = client.get("/projects/proj-123/quota")
    assert res.status_code == 200
    data = res.json()
    assert data["max_instances"] == 10
    assert data["max_vcpus"] == 20
    assert data["max_ram_mb"] == 32768
    assert data["max_volume_gb"] == 100

    # PUT update quota
    res = client.put(
        "/projects/proj-123/quota",
        json={"max_instances": 5, "max_vcpus": 8, "max_ram_mb": 16384, "max_volume_gb": 50},
    )
    assert res.status_code == 200
    updated = res.json()
    assert updated["max_instances"] == 5
    assert updated["max_vcpus"] == 8


def test_placement_usages_includes_instances(monkeypatch, tmp_path):
    db_path = tmp_path / "placement_usage.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    import services.placement.app.main as place_main

    place_main = importlib.reload(place_main)
    place_main.Base.metadata.create_all(bind=place_main.engine)

    from services.shared.auth_middleware import require_auth
    place_main.app.dependency_overrides[require_auth] = lambda: {"project_id": "proj-1", "role": "admin"}
    client = TestClient(place_main.app)

    with place_main.SessionLocal() as db:
        alloc1 = place_main.Allocation(
            consumer_uuid="vm-1",
            project_id="proj-1",
            resource_provider_id="rp-1",
            resource_class="VCPU",
            used=2,
        )
        alloc2 = place_main.Allocation(
            consumer_uuid="vm-1",
            project_id="proj-1",
            resource_provider_id="rp-1",
            resource_class="MEMORY_MB",
            used=4096,
        )
        db.add_all([alloc1, alloc2])
        db.commit()

    res = client.get("/usages?project_id=proj-1")
    assert res.status_code == 200
    payload = res.json()
    assert payload["usages"]["VCPU"] == 2
    assert payload["usages"]["MEMORY_MB"] == 4096
    assert payload["usages"]["instances"] == 1


def test_load_balancer_health_probe_logic(monkeypatch, tmp_path):
    db_path = tmp_path / "lb_health.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    import services.load_balancer.app.main as lb_main

    lb_main = importlib.reload(lb_main)
    lb_main.Base.metadata.create_all(bind=lb_main.engine)

    with lb_main.SessionLocal() as db:
        lb = lb_main.LoadBalancer(id="lb-1", name="lb-test", project_id="proj-1", lb_type="ALB")
        pool = lb_main.Pool(id="pool-1", load_balancer_id="lb-1", listener_id="ls-1", name="p1", protocol="HTTP")
        member = lb_main.Member(id="m-1", pool_id="pool-1", address="127.0.0.1", protocol_port=9999, operating_status="ONLINE")
        hm = lb_main.HealthMonitor(id="hm-1", pool_id="pool-1", type="TCP", delay=5, timeout=1, max_retries=2)
        db.add_all([lb, pool, member, hm])
        db.commit()

    # Stub driver apply state
    applied = []
    monkeypatch.setattr(lb_main, "_apply_lb_state", lambda db, lb: applied.append(lb.id))

    # Run health monitor check (member 127.0.0.1:9999 should fail TCP connection)
    lb_main._run_health_monitors()
    lb_main._run_health_monitors()

    with lb_main.SessionLocal() as db:
        m = db.get(lb_main.Member, "m-1")
        assert m.operating_status == "OFFLINE"
    assert len(applied) >= 1
