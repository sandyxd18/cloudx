import importlib
import os
from pathlib import Path

from fastapi.testclient import TestClient
from jose import jwt

AUTH = {"Authorization": "Bearer " + jwt.encode(
    {"project_id": "proj-1", "role": "member", "user_id": "usr-1"}, os.environ["AUTH_SECRET_KEY"], algorithm="HS256")}

def test_k8s_engine_crud(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "k8s_engine.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

    import services.k8s_engine.app.main as k8s_main
    k8s_main = importlib.reload(k8s_main)
    k8s_main.Base.metadata.create_all(bind=k8s_main.engine)

    client = TestClient(k8s_main.app)

    # 1. Health check
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"
    assert res.json()["service"] == "k8s-engine-api"

    # Requests without a valid token (even with a spoofed project header) are rejected
    assert client.get("/clusters", headers={"X-Project-Id": "admin"}).status_code == 401

    # 2. Create Cluster Template
    template_body = {
        "name": "k3s-v1.30-default",
        "k8s_version": "v1.30.2+k3s1",
        "master_flavor": "m1.medium",
        "worker_flavor": "m1.small",
        "image_id": "img-ubuntu-2204",
        "network_driver": "ovn",
        "volume_driver": "cloudx-csi"
    }
    res = client.post("/cluster-templates", json=template_body, headers=AUTH)
    assert res.status_code == 201
    tpl_data = res.json()
    assert tpl_data["name"] == "k3s-v1.30-default"
    tpl_id = tpl_data["id"]

    # 3. List & Get Cluster Templates
    res = client.get("/cluster-templates", headers=AUTH)
    assert res.status_code == 200
    assert len(res.json()) == 1

    res = client.get(f"/cluster-templates/{tpl_id}", headers=AUTH)
    assert res.status_code == 200
    assert res.json()["id"] == tpl_id

    # 4. Create Kubernetes Cluster
    cluster_body = {
        "name": "prod-k8s-cluster",
        "template_id": tpl_id,
        "master_count": 1,
        "worker_count": 2,
        "network_id": "net-prod-1"
    }
    res = client.post("/clusters", json=cluster_body, headers=AUTH)
    assert res.status_code == 201
    cls_data = res.json()
    assert cls_data["name"] == "prod-k8s-cluster"
    assert cls_data["status"] in ("BUILDING", "ACTIVE")
    cls_id = cls_data["id"]

    # 5. List & Get Clusters
    res = client.get("/clusters", headers=AUTH)
    assert res.status_code == 200
    assert len(res.json()) == 1

    res = client.get(f"/clusters/{cls_id}", headers=AUTH)
    assert res.status_code == 200
    assert res.json()["master_count"] == 1
    assert res.json()["worker_count"] == 2

    # 6. Scale Cluster
    scale_body = {"worker_count": 4}
    res = client.post(f"/clusters/{cls_id}/scale", json=scale_body, headers=AUTH)
    assert res.status_code == 200
    assert res.json()["worker_count"] == 4

    # 7. Get Kubeconfig
    res = client.get(f"/clusters/{cls_id}/kubeconfig", headers=AUTH)
    assert res.status_code == 200
    kubeconfig = res.json()["kubeconfig"]
    assert "apiVersion: v1" in kubeconfig
    assert "prod-k8s-cluster" in kubeconfig

    # 8. Delete Cluster & Template
    res = client.delete(f"/clusters/{cls_id}", headers=AUTH)
    assert res.status_code == 204

    res = client.delete(f"/cluster-templates/{tpl_id}", headers=AUTH)
    assert res.status_code == 204
