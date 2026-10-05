from fastapi.testclient import TestClient

from tests.stack import fresh_service


def test_auth_project_quota_crud(monkeypatch, tmp_path):
    db_path = tmp_path / "auth_quota.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    auth_main = fresh_service("auth", monkeypatch, tmp_path)

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
