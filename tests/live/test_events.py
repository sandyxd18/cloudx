from __future__ import annotations

import os
import uuid

import requests

EVENT_URL = os.getenv("EVENT_API_URL", "http://127.0.0.1:8012")

class TestEventAPI:
    def test_health(self):
        r = requests.get(f"{EVENT_URL}/health", timeout=10)
        assert r.status_code in (200, 201)
        assert r.json()["service"] == "event-api"

    def test_event_types(self):
        r = requests.get(f"{EVENT_URL}/events/types", timeout=10)
        assert r.status_code in (200, 201)
        result = r.json()
        assert len(result["event_types"]) > 0
        event_names = [e["type"] for e in result["event_types"]]
        assert "instance.created" in event_names
        assert "instance.crashed" in event_names

    def test_create_subscription(self, auth_headers):
        data = {
            "name": "test-webhook",
            "endpoint_url": "https://httpbin.org/post",
            "event_types": ["instance.created", "instance.deleted"],
        }
        r = requests.post(
            f"{EVENT_URL}/events/subscriptions",
            headers=auth_headers,
            json=data,
            timeout=10,
        )
        assert r.status_code == 201
        result = r.json()
        assert result["name"] == "test-webhook"
        assert result["endpoint_url"] == "https://httpbin.org/post"
        return result["id"]

    def test_list_subscriptions(self, auth_headers):
        r = requests.get(
            f"{EVENT_URL}/events/subscriptions",
            headers=auth_headers,
            timeout=10,
        )
        assert r.status_code in (200, 201)
        subs = r.json()
        assert isinstance(subs, list)

    def test_deliver_event(self, auth_headers, test_project_id):
        data = {
            "event_type": "instance.created",
            "payload": {
                "instance_id": str(uuid.uuid4()),
                "name": "test-vm",
                "flavor": "m1.small",
            },
            "project_id": test_project_id,
        }
        r = requests.post(
            f"{EVENT_URL}/events/deliver",
            headers=auth_headers,
            json=data,
            timeout=10,
        )
        assert r.status_code == 202
        result = r.json()
        assert "event_id" in result

    def test_list_deliveries(self, auth_headers):
        r = requests.get(
            f"{EVENT_URL}/events/deliveries?limit=10",
            headers=auth_headers,
            timeout=10,
        )
        assert r.status_code in (200, 201)
        deliveries = r.json()
        assert isinstance(deliveries, list)
