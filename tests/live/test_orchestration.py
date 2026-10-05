from __future__ import annotations

import os
import uuid

import requests

ORCHESTRATION_URL = os.getenv("ORCHESTRATION_API_URL", "http://127.0.0.1:8013")

class TestOrchestration:
    def test_health(self):
        r = requests.get(f"{ORCHESTRATION_URL}/health", timeout=10)
        assert r.status_code in (200, 201)
        assert r.json()["service"] == "orchestration-api"

    def test_create_scaling_group(self, auth_headers):
        data = {
            "name": f"test-asg-{uuid.uuid4().hex[:6]}",
            "description": "Test auto-scaling group",
            "min_instances": 1,
            "max_instances": 3,
            "target_cpu_percent": 70,
            "cooldown_seconds": 60,
            "launch_template_id": str(uuid.uuid4()),
            "image_id": str(uuid.uuid4()),
            "flavor_id": str(uuid.uuid4()),
            "network_id": str(uuid.uuid4()),
        }
        r = requests.post(
            f"{ORCHESTRATION_URL}/scaling-groups",
            headers=auth_headers,
            json=data,
            timeout=10,
        )
        assert r.status_code == 201
        result = r.json()
        assert result["name"] == data["name"]
        assert result["min_instances"] == 1
        assert result["max_instances"] == 3

    def test_list_scaling_groups(self, auth_headers):
        r = requests.get(
            f"{ORCHESTRATION_URL}/scaling-groups",
            headers=auth_headers,
            timeout=10,
        )
        assert r.status_code in (200, 201)
        assert isinstance(r.json(), list)
