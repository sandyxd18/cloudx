from __future__ import annotations

import os
import uuid

import requests

TELEMETRY_URL = os.getenv("TELEMETRY_API_URL", "http://127.0.0.1:8010")

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
