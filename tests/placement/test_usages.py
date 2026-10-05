import importlib

from fastapi.testclient import TestClient


def test_placement_usages_includes_instances(monkeypatch, tmp_path):
    db_path = tmp_path / "placement_usage.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    import services.placement.app.main as place_main

    place_main = importlib.reload(place_main)
    place_main.Base.metadata.create_all(bind=place_main.engine)

    from shared.auth_middleware import require_auth
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
