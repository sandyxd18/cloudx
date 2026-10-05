from __future__ import annotations

import os
import uuid

import requests

PLACEMENT_URL = os.getenv("PLACEMENT_API_URL", "http://127.0.0.1:8006")

class TestPlacementMultiNode:
    def test_health(self):
        r = requests.get(f"{PLACEMENT_URL}/health", timeout=10)
        assert r.status_code in (200, 201)

    def test_create_resource_provider(self, auth_headers):
        data = {"name": f"compute-node-{uuid.uuid4().hex[:6]}"}
        r = requests.post(
            f"{PLACEMENT_URL}/resource_providers",
            headers=auth_headers,
            json=data,
            timeout=10,
        )
        assert r.status_code == 201
        result = r.json()
        assert "uuid" in result
        assert result["name"] == data["name"]
        return result["uuid"]

    def test_set_inventories(self, auth_headers):
        # Create RP
        rp_resp = requests.post(
            f"{PLACEMENT_URL}/resource_providers",
            headers=auth_headers,
            json={"name": f"sched-test-{uuid.uuid4().hex[:6]}"},
            timeout=10,
        )
        rp_uuid = rp_resp.json()["uuid"]

        data = {
            "inventories": {
                "VCPU": {"total": 8, "reserved": 0, "allocation_ratio": 1.0},
                "MEMORY_MB": {"total": 16384, "reserved": 512, "allocation_ratio": 1.0},
                "DISK_GB": {"total": 500, "reserved": 0, "allocation_ratio": 1.0},
            }
        }
        r = requests.put(
            f"{PLACEMENT_URL}/resource_providers/{rp_uuid}/inventories",
            headers=auth_headers,
            json=data,
            timeout=10,
        )
        assert r.status_code in (200, 201)
        result = r.json()
        assert "inventories" in result
        assert result["inventories"]["VCPU"]["total"] == 8

    def test_heartbeat(self, auth_headers):
        rp_resp = requests.post(
            f"{PLACEMENT_URL}/resource_providers",
            headers=auth_headers,
            json={"name": f"heartbeat-test-{uuid.uuid4().hex[:6]}"},
            timeout=10,
        )
        rp_uuid = rp_resp.json()["uuid"]

        data = {
            "host": "compute-node-02",
            "cpu_percent_used": 25.0,
            "memory_mb_used": 4096,
            "memory_mb_total": 16384,
            "disk_gb_used": 50,
            "disk_gb_total": 500,
            "running_vms": 2,
        }
        r = requests.post(
            f"{PLACEMENT_URL}/resource_providers/{rp_uuid}/heartbeat",
            headers=auth_headers,
            json=data,
            timeout=10,
        )
        assert r.status_code in (200, 201)
        assert r.json()["resource_provider_uuid"] == rp_uuid

    def test_scheduler_select_destination(self, auth_headers):
        data = {
            "resources": {"VCPU": 2, "MEMORY_MB": 4096, "DISK_GB": 20},
            "strategy": "memory",
        }
        r = requests.post(
            f"{PLACEMENT_URL}/scheduler/select_destination",
            headers=auth_headers,
            json=data,
            timeout=10,
        )
        assert r.status_code in (200, 201)
        result = r.json()
        assert "selected_host" in result
        assert "resource_provider_uuid" in result
