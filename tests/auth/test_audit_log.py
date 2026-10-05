from fastapi.testclient import TestClient

from tests.stack import fresh_service


def test_audit_logging(monkeypatch, tmp_path):
    db_path = tmp_path / "auth_audit.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    auth_main = fresh_service("auth", monkeypatch, tmp_path)

    auth_main.app.dependency_overrides[auth_main._require_token] = lambda: {"project_id": "proj-1", "role": "admin", "username": "admin"}
    client = TestClient(auth_main.app)

    # Create audit log entry
    res_create = client.post(
        "/audit-logs",
        json={
            "username": "admin",
            "project_id": "proj-1",
            "action": "server.create",
            "resource_type": "server",
            "resource_id": "srv-999",
            "details": "Created VM instance srv-999",
        },
    )
    assert res_create.status_code == 201
    data = res_create.json()
    assert data["action"] == "server.create"
    assert data["resource_id"] == "srv-999"

    # List audit logs
    res_list = client.get("/audit-logs?project_id=proj-1")
    assert res_list.status_code == 200
    items = res_list.json()["items"]
    assert len(items) == 1
    assert items[0]["resource_type"] == "server"
