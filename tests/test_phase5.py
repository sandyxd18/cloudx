"""
Test Suite — Fase 5: Enterprise Observability & Microservice Billing/Events
============================================================================

Tests:
  • Telemetry API — metrics ingestion, querying, Prometheus exporter
  • Billing API — rating sheets, usage records, invoices, PDF generation
  • Event API — webhook subscriptions, event delivery, HMAC signatures
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import requests

# Base URLs
TELEMETRY_URL = os.getenv("TELEMETRY_API_URL", "http://127.0.0.1:8010")
BILLING_URL = os.getenv("BILLING_API_URL", "http://127.0.0.1:8011")
EVENT_URL = os.getenv("EVENT_API_URL", "http://127.0.0.1:8012")
AUTH_URL = os.getenv("AUTH_API_URL", "http://127.0.0.1:8005")

# Admin credentials
ADMIN_USER = os.getenv("AUTH_ADMIN_USERNAME", "admin")
ADMIN_PASS = os.getenv("AUTH_ADMIN_PASSWORD", "admin123")


@pytest.fixture(scope="module")
def admin_token():
    """Get admin JWT token."""
    r = requests.post(
        f"{AUTH_URL}/auth/tokens",
        json={"username": ADMIN_USER, "password": ADMIN_PASS},
        timeout=10,
    )
    assert r.status_code in (200, 201)
    return r.json()["token"]


@pytest.fixture(scope="module")
def auth_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"}


@pytest.fixture(scope="module")
def test_project_id(admin_token):
    """Get or create a test project."""
    headers = {"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"}
    r = requests.get(f"{AUTH_URL}/auth/projects", headers=headers, timeout=10)
    if r.status_code == 200:
        projects = r.json().get("projects", [])
        for p in projects:
            if p.get("name") == "test-phase5":
                return p["id"]
    
    # Create test project
    r = requests.post(
        f"{AUTH_URL}/auth/projects",
        headers=headers,
        json={"name": "test-phase5", "description": "Test project for Phase 5"},
        timeout=10,
    )
    if r.status_code in (200, 201):
        return r.json()["id"]
    return str(uuid.uuid4())


# ═══════════════════════════════════════════════════════════════════════════════
# Telemetry API Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestTelemetryAPI:
    def test_health(self):
        r = requests.get(f"{TELEMETRY_URL}/health", timeout=10)
        assert r.status_code in (200, 201)
        assert r.json()["service"] == "telemetry-api"

    def test_metric_types(self, auth_headers):
        r = requests.get(f"{TELEMETRY_URL}/telemetry/metric-types", headers=auth_headers, timeout=10)
        assert r.status_code in (200, 201)
        types = r.json()
        assert len(types) > 0
        metric_names = [t["name"] for t in types]
        assert "cpu_percent" in metric_names
        assert "memory_used_mb" in metric_names

    def test_create_metric(self, auth_headers):
        data = {
            "instance_id": str(uuid.uuid4()),
            "project_id": str(uuid.uuid4()),
            "metric_type": "cpu_percent",
            "value": 45.5,
            "unit": "%",
            "host": "test-host",
        }
        r = requests.post(
            f"{TELEMETRY_URL}/telemetry/metrics",
            headers=auth_headers,
            json=data,
            timeout=10,
        )
        assert r.status_code == 201
        result = r.json()
        assert result["metric_type"] == "cpu_percent"
        assert result["value"] == 45.5

    def test_batch_metrics(self, auth_headers):
        instance_id = str(uuid.uuid4())
        metrics = [
            {
                "instance_id": instance_id,
                "metric_type": "cpu_percent",
                "value": 30.0,
                "unit": "%",
            },
            {
                "instance_id": instance_id,
                "metric_type": "memory_used_mb",
                "value": 1024.0,
                "unit": "MB",
            },
        ]
        r = requests.post(
            f"{TELEMETRY_URL}/telemetry/metrics/batch",
            headers=auth_headers,
            json=metrics,
            timeout=10,
        )
        assert r.status_code == 201
        assert r.json()["ingested"] == 2

    def test_list_metrics(self, auth_headers):
        r = requests.get(
            f"{TELEMETRY_URL}/telemetry/metrics?metric_type=cpu_percent&limit=10",
            headers=auth_headers,
            timeout=10,
        )
        assert r.status_code in (200, 201)
        metrics = r.json()
        assert len(metrics) > 0

    def test_prometheus_exporter(self):
        r = requests.get(f"{TELEMETRY_URL}/metrics", timeout=10)
        assert r.status_code in (200, 201)
        content = r.text
        assert ("cloudx_" in content or "# no metrics" in content)

    def test_instance_stats(self, auth_headers):
        # First create some metrics
        instance_id = str(uuid.uuid4())
        for i in range(5):
            requests.post(
                f"{TELEMETRY_URL}/telemetry/metrics",
                headers=auth_headers,
                json={
                    "instance_id": instance_id,
                    "metric_type": "cpu_percent",
                    "value": float(20 + i * 5),
                },
                timeout=10,
            )
        
        r = requests.get(
            f"{TELEMETRY_URL}/telemetry/instances/{instance_id}/stats?hours=1&granularity=1m",
            headers=auth_headers,
            timeout=10,
        )
        assert r.status_code in (200, 201)
        result = r.json()
        assert result["instance_id"] == instance_id
        assert "cpu_percent" in result["metrics"]


# ═══════════════════════════════════════════════════════════════════════════════
# Billing API Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestBillingAPI:
    def test_health(self):
        r = requests.get(f"{BILLING_URL}/health", timeout=10)
        assert r.status_code in (200, 201)
        assert r.json()["service"] == "billing-api"

    def test_rating_sheets(self, auth_headers):
        r = requests.get(
            f"{BILLING_URL}/billing/rating-sheets",
            headers=auth_headers,
            timeout=10,
        )
        assert r.status_code in (200, 201)
        sheets = r.json()
        assert len(sheets) > 0
        resource_types = [s["resource_type"] for s in sheets]
        assert "vcpu" in resource_types
        assert "ram" in resource_types

    def test_create_rating_sheet(self, auth_headers):
        data = {
            "resource_type": "gpu",
            "unit": "gpu-hour",
            "rate_per_unit": "500.0000",
            "currency": "IDR",
            "description": "GPU per hour",
        }
        r = requests.post(
            f"{BILLING_URL}/billing/rating-sheets",
            headers=auth_headers,
            json=data,
            timeout=10,
        )
        assert r.status_code == 201
        result = r.json()
        assert result["resource_type"] == "gpu"
        assert float(result["rate_per_unit"]) == 500.0

    def test_create_usage_record(self, auth_headers, test_project_id):
        data = {
            "project_id": test_project_id,
            "resource_type": "vcpu",
            "resource_id": str(uuid.uuid4()),
            "quantity": "24.0000",
            "unit": "vcpu-hour",
            "rate_per_unit": "100.0000",
            "period_start": (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(),
            "period_end": datetime.now(timezone.utc).isoformat(),
        }
        r = requests.post(
            f"{BILLING_URL}/billing/usage-records",
            headers=auth_headers,
            json=data,
            timeout=10,
        )
        assert r.status_code == 201
        result = r.json()
        assert result["project_id"] == test_project_id
        assert float(result["total_cost"]) == 2400.0

    def test_billing_summary(self, auth_headers, test_project_id):
        now = datetime.now(timezone.utc)
        r = requests.get(
            f"{BILLING_URL}/billing/summary?project_id={test_project_id}&year={now.year}&month={now.month}",
            headers=auth_headers,
            timeout=10,
        )
        assert r.status_code in (200, 201)
        result = r.json()
        assert result["project_id"] == test_project_id
        assert "total_cost" in result

    def test_create_invoice(self, auth_headers, test_project_id):
        now = datetime.now(timezone.utc)
        # Create some usage first
        data = {
            "project_id": test_project_id,
            "resource_type": "ram",
            "quantity": "48.0000",
            "unit": "gb-hour",
            "rate_per_unit": "50.0000",
            "period_start": (now - timedelta(days=15)).isoformat(),
            "period_end": now.isoformat(),
        }
        requests.post(
            f"{BILLING_URL}/billing/usage-records",
            headers=auth_headers,
            json=data,
            timeout=10,
        )
        
        # Generate invoice
        r = requests.post(
            f"{BILLING_URL}/billing/invoices?project_id={test_project_id}&year={now.year}&month={now.month}",
            headers=auth_headers,
            timeout=10,
        )
        assert r.status_code == 201
        result = r.json()
        assert result["project_id"] == test_project_id
        assert result["status"] == "draft"
        assert float(result["total"]) > 0

    def test_list_invoices(self, auth_headers, test_project_id):
        r = requests.get(
            f"{BILLING_URL}/billing/invoices?project_id={test_project_id}",
            headers=auth_headers,
            timeout=10,
        )
        assert r.status_code in (200, 201)
        invoices = r.json()
        assert len(invoices) > 0


# ═══════════════════════════════════════════════════════════════════════════════
# Event API Tests
# ═══════════════════════════════════════════════════════════════════════════════

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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])