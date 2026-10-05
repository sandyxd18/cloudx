#!/usr/bin/env python3
"""
Test Phase 8 (Managed Kubernetes Engine) against live CloudX services on VM.
"""

import os
import sys
import json
import time
import requests

K8S_ENGINE_URL = "http://127.0.0.1:8015"
AUTH_URL       = "http://127.0.0.1:8005"

def get_auth_token():
    auth_resp = requests.post(f"{AUTH_URL}/auth/tokens", json={"username": "admin", "password": os.environ["AUTH_ADMIN_PASSWORD"]})
    if auth_resp.status_code == 201:
        token = auth_resp.json().get("token")
        project_id = auth_resp.json().get("project", {}).get("id", "admin")
        return token, project_id
    raise RuntimeError(f"Failed to obtain auth token: {auth_resp.text}")

token, project_id = get_auth_token()

HEADERS_ADMIN = {
    "X-Auth-Token": token,
    "X-Project-Id": project_id,
    "X-Role": "admin"
}

def log(msg, status="INFO"):
    symbol = "✓" if status == "OK" else "❌" if status == "FAIL" else "ℹ"
    print(f"[{symbol}] {msg}")

def test_k8s_engine_live():
    log("=== Testing Kubernetes Engine API (Port 8015) ===")

    # 1. Health Check
    res = requests.get(f"{K8S_ENGINE_URL}/health")
    assert res.status_code == 200, f"Health check failed: {res.text}"
    log(f"Health check ok: {res.json()}", "OK")

    # 2. Create Cluster Template
    tpl_payload = {
        "name": "live-k3s-v1.30",
        "k8s_version": "v1.30.2+k3s1",
        "master_flavor": "m1.medium",
        "worker_flavor": "m1.small",
        "image_id": "img-ubuntu-2204",
        "network_driver": "ovn",
        "volume_driver": "cloudx-csi"
    }
    res = requests.post(f"{K8S_ENGINE_URL}/cluster-templates", json=tpl_payload, headers=HEADERS_ADMIN)
    assert res.status_code == 201, f"Create template failed: {res.text}"
    tpl_data = res.json()
    tpl_id = tpl_data["id"]
    log(f"Cluster Template created: ID={tpl_id}, Name={tpl_data['name']}", "OK")

    # 3. List & Show Cluster Template
    res = requests.get(f"{K8S_ENGINE_URL}/cluster-templates", headers=HEADERS_ADMIN)
    assert res.status_code == 200
    templates = res.json()
    assert any(t["id"] == tpl_id for t in templates)
    log(f"List templates ok: found {len(templates)} template(s)", "OK")

    res = requests.get(f"{K8S_ENGINE_URL}/cluster-templates/{tpl_id}", headers=HEADERS_ADMIN)
    assert res.status_code == 200
    log(f"Get template detail ok: version={res.json()['k8s_version']}", "OK")

    # 4. Create K8s Cluster
    cls_payload = {
        "name": "live-k8s-prod-cluster",
        "template_id": tpl_id,
        "master_count": 1,
        "worker_count": 2,
        "network_id": "net-live-01"
    }
    res = requests.post(f"{K8S_ENGINE_URL}/clusters", json=cls_payload, headers=HEADERS_ADMIN)
    assert res.status_code == 201, f"Create cluster failed: {res.text}"
    cls_data = res.json()
    cls_id = cls_data["id"]
    log(f"Kubernetes Cluster created: ID={cls_id}, Name={cls_data['name']}", "OK")

    # 5. List & Show Cluster
    res = requests.get(f"{K8S_ENGINE_URL}/clusters", headers=HEADERS_ADMIN)
    assert res.status_code == 200
    clusters = res.json()
    assert any(c["id"] == cls_id for c in clusters)
    log(f"List clusters ok: found {len(clusters)} cluster(s)", "OK")

    res = requests.get(f"{K8S_ENGINE_URL}/clusters/{cls_id}", headers=HEADERS_ADMIN)
    assert res.status_code == 200
    assert res.json()["worker_count"] == 2
    log("Get cluster detail ok", "OK")

    # 6. Scale Cluster
    scale_payload = {"worker_count": 5}
    res = requests.post(f"{K8S_ENGINE_URL}/clusters/{cls_id}/scale", json=scale_payload, headers=HEADERS_ADMIN)
    assert res.status_code == 200, f"Scale cluster failed: {res.text}"
    assert res.json()["worker_count"] == 5
    log("Scale cluster ok: worker count scaled to 5", "OK")

    # 7. Get Kubeconfig
    res = requests.get(f"{K8S_ENGINE_URL}/clusters/{cls_id}/kubeconfig", headers=HEADERS_ADMIN)
    assert res.status_code == 200, f"Get kubeconfig failed: {res.text}"
    kubeconfig = res.json()["kubeconfig"]
    assert "apiVersion: v1" in kubeconfig
    assert "live-k8s-prod-cluster" in kubeconfig
    log("Kubeconfig generated & verified successfully", "OK")

    # 8. Delete Cluster & Template
    res = requests.delete(f"{K8S_ENGINE_URL}/clusters/{cls_id}", headers=HEADERS_ADMIN)
    assert res.status_code == 204
    log(f"Deleted cluster {cls_id}", "OK")

    res = requests.delete(f"{K8S_ENGINE_URL}/cluster-templates/{tpl_id}", headers=HEADERS_ADMIN)
    assert res.status_code == 204
    log(f"Deleted template {tpl_id}", "OK")

if __name__ == "__main__":
    try:
        test_k8s_engine_live()
        print()
        log("🎉 ALL PHASE 8 LIVE VM SERVICE TESTS PASSED SUCCESSFULLY!", "OK")
    except AssertionError as e:
        log(f"ASSERTION FAILED: {e}", "FAIL")
        sys.exit(1)
    except Exception as e:
        log(f"UNEXPECTED ERROR: {e}", "FAIL")
        sys.exit(1)
