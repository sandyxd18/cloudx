import importlib
import os
from pathlib import Path

from fastapi.testclient import TestClient
from jose import jwt

AUTH = {"Authorization": "Bearer " + jwt.encode(
    {"project_id": "proj-admin", "role": "admin", "user_id": "usr-1"}, os.environ["AUTH_SECRET_KEY"], algorithm="HS256")}

def test_key_manager_secrets_crud(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "key_manager.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("KEY_MANAGER_MASTER_KEY", "super-secret-master-key-32bytes-len!!")

    import services.key_manager.app.main as km_main
    km_main = importlib.reload(km_main)
    km_main.Base.metadata.create_all(bind=km_main.engine)

    client = TestClient(km_main.app)

    # 1. Health check
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"
    assert res.json()["service"] == "key-manager-api"

    # 2. Create Secret (Symmetric Key)
    payload = {
        "name": "luks-disk-key-1",
        "secret_type": "symmetric",
        "payload": "super-secret-passphrase-12345",
        "algorithm": "aes",
        "bit_length": 256
    }
    res = client.post("/secrets", json=payload, headers=AUTH)
    assert res.status_code == 201
    secret_data = res.json()
    assert secret_data["name"] == "luks-disk-key-1"
    assert secret_data["secret_type"] == "symmetric"
    secret_id = secret_data["id"]

    # 3. List Secrets
    res = client.get("/secrets", headers=AUTH)
    assert res.status_code == 200
    secrets_list = res.json()
    assert len(secrets_list) == 1
    assert secrets_list[0]["id"] == secret_id

    # 4. Get Secret Detail (with decrypted payload)
    res = client.get(f"/secrets/{secret_id}", headers=AUTH)
    assert res.status_code == 200
    detail = res.json()
    assert detail["payload"] == "super-secret-passphrase-12345"

    # 5. Create Container (Grouping secrets)
    container_payload = {
        "name": "tls-cert-bundle",
        "container_type": "certificate",
        "secret_refs": [secret_id]
    }
    res = client.post("/containers", json=container_payload, headers=AUTH)
    assert res.status_code == 201
    container_data = res.json()
    assert container_data["name"] == "tls-cert-bundle"
    container_id = container_data["id"]

    # 6. List Containers
    res = client.get("/containers", headers=AUTH)
    assert res.status_code == 200
    containers = res.json()
    assert len(containers) == 1
    assert containers[0]["id"] == container_id

    # 7. Delete Secret & Container
    res = client.delete(f"/containers/{container_id}", headers=AUTH)
    assert res.status_code == 204

    res = client.delete(f"/secrets/{secret_id}", headers=AUTH)
    assert res.status_code == 204

    # Verify deletion
    res = client.get(f"/secrets/{secret_id}", headers=AUTH)
    assert res.status_code == 404
