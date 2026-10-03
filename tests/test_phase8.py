import importlib
import os
import subprocess
import sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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
    res = client.post("/cluster-templates", json=template_body, headers={"X-Project-Id": "proj-1"})
    assert res.status_code == 201
    tpl_data = res.json()
    assert tpl_data["name"] == "k3s-v1.30-default"
    tpl_id = tpl_data["id"]

    # 3. List & Get Cluster Templates
    res = client.get("/cluster-templates", headers={"X-Project-Id": "proj-1"})
    assert res.status_code == 200
    assert len(res.json()) == 1

    res = client.get(f"/cluster-templates/{tpl_id}", headers={"X-Project-Id": "proj-1"})
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
    res = client.post("/clusters", json=cluster_body, headers={"X-Project-Id": "proj-1"})
    assert res.status_code == 201
    cls_data = res.json()
    assert cls_data["name"] == "prod-k8s-cluster"
    assert cls_data["status"] in ("BUILDING", "ACTIVE")
    cls_id = cls_data["id"]

    # 5. List & Get Clusters
    res = client.get("/clusters", headers={"X-Project-Id": "proj-1"})
    assert res.status_code == 200
    assert len(res.json()) == 1

    res = client.get(f"/clusters/{cls_id}", headers={"X-Project-Id": "proj-1"})
    assert res.status_code == 200
    assert res.json()["master_count"] == 1
    assert res.json()["worker_count"] == 2

    # 6. Scale Cluster
    scale_body = {"worker_count": 4}
    res = client.post(f"/clusters/{cls_id}/scale", json=scale_body, headers={"X-Project-Id": "proj-1"})
    assert res.status_code == 200
    assert res.json()["worker_count"] == 4

    # 7. Get Kubeconfig
    res = client.get(f"/clusters/{cls_id}/kubeconfig", headers={"X-Project-Id": "proj-1"})
    assert res.status_code == 200
    kubeconfig = res.json()["kubeconfig"]
    assert "apiVersion: v1" in kubeconfig
    assert "prod-k8s-cluster" in kubeconfig

    # 8. Delete Cluster & Template
    res = client.delete(f"/clusters/{cls_id}", headers={"X-Project-Id": "proj-1"})
    assert res.status_code == 204

    res = client.delete(f"/cluster-templates/{tpl_id}", headers={"X-Project-Id": "proj-1"})
    assert res.status_code == 204

def test_cloudx_csi_driver_provisioner():
    import services.k8s_engine.app.csi_driver as csi
    pvc_request = {
        "pv_name": "pvc-claim-12345",
        "capacity_bytes": 10737418240, # 10Gi
        "volume_type": "gp2",
        "project_id": "proj-1"
    }
    vol_payload = csi.create_csi_volume_payload(pvc_request)
    assert vol_payload["name"] == "pvc-claim-12345"
    assert vol_payload["size_gb"] == 10
    assert vol_payload["volume_type"] == "gp2"

def test_cloudx_ccm_service_provisioner():
    import services.k8s_engine.app.ccm_driver as ccm
    k8s_svc = {
        "service_name": "k8s-ingress-nginx",
        "namespace": "ingress-nginx",
        "port": 80,
        "target_port": 30080,
        "node_ips": ["10.0.0.15", "10.0.0.16"],
        "project_id": "proj-1"
    }
    lb_payload = ccm.create_ccm_loadbalancer_payload(k8s_svc)
    assert lb_payload["name"] == "ccm-ingress-nginx"
    assert lb_payload["protocol"] == "HTTP"
    assert len(lb_payload["members"]) == 2

def test_k8s_engine_cli_help():
    cli_main = Path(__file__).resolve().parents[1] / "cli" / "cloudx_cli" / "main.py"
    res = subprocess.run([sys.executable, str(cli_main), "k8s-template", "--help"], capture_output=True, text=True)
    assert res.returncode == 0
    assert "k8s-template" in res.stdout

    res2 = subprocess.run([sys.executable, str(cli_main), "cluster", "--help"], capture_output=True, text=True)
    assert res2.returncode == 0
    assert "cluster" in res2.stdout
