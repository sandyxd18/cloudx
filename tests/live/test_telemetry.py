from __future__ import annotations

import os
import uuid

import pytest
import requests

from tests.live.conftest import AUTH_URL

TELEMETRY_URL = os.getenv("TELEMETRY_API_URL", "http://127.0.0.1:8010")


@pytest.fixture(scope="module")
def member(auth_headers):
    """Member user in a fresh project (own token); skips if the auth API will not create one.
    Reused by test_billing / test_events."""
    tag = uuid.uuid4().hex[:8]
    pr = requests.post(f"{AUTH_URL}/projects", headers=auth_headers, json={"name": f"scope-{tag}"}, timeout=10)
    if pr.status_code not in (200, 201):
        pytest.skip(f"cannot create second project: {pr.status_code}")
    pid = pr.json()["id"]
    ur = requests.post(f"{AUTH_URL}/users", headers=auth_headers, timeout=10, json={
        "username": f"scope-{tag}", "email": f"scope-{tag}@example.com", "password": "Scope-pass-123", "project_id": pid})
    try:
        if ur.status_code not in (200, 201):
            pytest.skip(f"cannot create second user: {ur.status_code}")
        lr = requests.post(f"{AUTH_URL}/auth/tokens", json={"username": f"scope-{tag}", "password": "Scope-pass-123"}, timeout=10)
        if lr.status_code not in (200, 201):
            pytest.skip(f"cannot log in as second user: {lr.status_code}")
        yield {"project_id": pid, "headers": {"Authorization": f"Bearer {lr.json()['token']}"}}
    finally:
        if ur.status_code in (200, 201):
            requests.delete(f"{AUTH_URL}/users/{ur.json()['id']}", headers=auth_headers, timeout=10)
        requests.delete(f"{AUTH_URL}/projects/{pid}", headers=auth_headers, timeout=10)


class TestTelemetryAPI:
    def test_health(self):
        r = requests.get(f"{TELEMETRY_URL}/health", timeout=10)
        assert r.status_code in (200, 201)
        assert r.json()["service"] == "telemetry-api"

    def test_metric_types(self, auth_headers):
        r = requests.get(f"{TELEMETRY_URL}/telemetry/metric-types", headers=auth_headers, timeout=10)
        assert r.status_code == 200
        names = [t["name"] for t in r.json()]
        assert "cpu_percent" in names
        assert "memory_used_mb" in names

    def test_create_metric(self, auth_headers):
        data = {
            "instance_id": str(uuid.uuid4()),
            "project_id": str(uuid.uuid4()),
            "metric_type": "cpu_percent",
            "value": 45.5,
            "unit": "%",
            "host": "test-host",
        }
        r = requests.post(f"{TELEMETRY_URL}/telemetry/metrics", headers=auth_headers, json=data, timeout=10)
        assert r.status_code == 201
        assert r.json()["metric_type"] == "cpu_percent"
        assert r.json()["value"] == 45.5

    def test_batch_metrics(self, auth_headers):
        instance_id = str(uuid.uuid4())
        metrics = [
            {"instance_id": instance_id, "metric_type": "cpu_percent", "value": 30.0, "unit": "%"},
            {"instance_id": instance_id, "metric_type": "memory_used_mb", "value": 1024.0, "unit": "MB"},
        ]
        r = requests.post(f"{TELEMETRY_URL}/telemetry/metrics/batch", headers=auth_headers, json=metrics, timeout=10)
        assert r.status_code == 201
        assert r.json()["ingested"] == 2

    def test_list_metrics_pagination_and_window(self, auth_headers):
        r = requests.get(
            f"{TELEMETRY_URL}/telemetry/metrics?metric_type=cpu_percent&limit=1&offset=0&since=2000-01-01T00:00:00Z",
            headers=auth_headers, timeout=10,
        )
        assert r.status_code == 200
        assert len(r.json()) == 1
        r = requests.get(
            f"{TELEMETRY_URL}/telemetry/metrics?until=2000-01-01T00:00:00Z", headers=auth_headers, timeout=10,
        )
        assert r.status_code == 200
        assert r.json() == []

    def test_stats_and_series(self, auth_headers):
        instance_id, pid = str(uuid.uuid4()), str(uuid.uuid4())
        batch = [{"instance_id": instance_id, "project_id": pid, "metric_type": "cpu_percent", "value": float(20 + i * 5)}
                 for i in range(5)]
        assert requests.post(f"{TELEMETRY_URL}/telemetry/metrics/batch", headers=auth_headers, json=batch, timeout=10).status_code == 201

        r = requests.get(f"{TELEMETRY_URL}/telemetry/stats?instance_id={instance_id}&hours=1", headers=auth_headers, timeout=10)
        assert r.status_code == 200
        rows = r.json()
        assert isinstance(rows, list) and len(rows) == 1
        row = rows[0]
        assert row["metric_type"] == "cpu_percent" and row["instance_id"] == instance_id
        assert row["count"] == 5 and row["min"] == 20.0 and row["max"] == 40.0
        assert "avg" in row and "last" in row

        r = requests.get(
            f"{TELEMETRY_URL}/telemetry/series?metric_type=cpu_percent&instance_id={instance_id}&granularity=raw&hours=1",
            headers=auth_headers, timeout=10,
        )
        assert r.status_code == 200
        assert len(r.json()) == 5

        r = requests.get(
            f"{TELEMETRY_URL}/telemetry/instances/{instance_id}/stats?hours=1", headers=auth_headers, timeout=10,
        )
        assert r.status_code == 200
        assert r.json()["instance_id"] == instance_id
        assert "cpu_percent" in r.json()["metrics"]

    def test_prometheus_exporter_is_open(self):
        r = requests.get(f"{TELEMETRY_URL}/metrics", timeout=10)
        assert r.status_code == 200
        assert "cloudx_" in r.text or "# no metrics" in r.text

    def test_ingest_requires_admin(self, member):
        body = {"instance_id": str(uuid.uuid4()), "metric_type": "cpu_percent", "value": 1.0}
        r = requests.post(f"{TELEMETRY_URL}/telemetry/metrics", headers=member["headers"], json=body, timeout=10)
        assert r.status_code == 403
        r = requests.post(f"{TELEMETRY_URL}/telemetry/metrics/batch", headers=member["headers"], json=[body], timeout=10)
        assert r.status_code == 403

    def test_non_admin_scoped_to_own_project(self, auth_headers, member, test_project_id):
        other_inst, own_inst = str(uuid.uuid4()), str(uuid.uuid4())
        batch = [
            {"instance_id": other_inst, "project_id": test_project_id, "metric_type": "cpu_percent", "value": 1.0},
            {"instance_id": own_inst, "project_id": member["project_id"], "metric_type": "cpu_percent", "value": 2.0},
        ]
        assert requests.post(f"{TELEMETRY_URL}/telemetry/metrics/batch", headers=auth_headers, json=batch, timeout=10).status_code == 201
        h = member["headers"]

        r = requests.get(f"{TELEMETRY_URL}/telemetry/metrics?instance_id={other_inst}", headers=h, timeout=10)
        assert r.status_code == 200 and r.json() == []
        # ?project_id is admin-only: ignored for a member
        r = requests.get(f"{TELEMETRY_URL}/telemetry/metrics?project_id={test_project_id}", headers=h, timeout=10)
        assert r.status_code == 200
        assert all(m["project_id"] == member["project_id"] for m in r.json())
        r = requests.get(f"{TELEMETRY_URL}/telemetry/stats?instance_id={other_inst}", headers=h, timeout=10)
        assert r.status_code == 200 and r.json() == []
        r = requests.get(
            f"{TELEMETRY_URL}/telemetry/series?metric_type=cpu_percent&instance_id={other_inst}&granularity=raw", headers=h, timeout=10)
        assert r.status_code == 200 and r.json() == []
        assert requests.get(f"{TELEMETRY_URL}/telemetry/instances/{other_inst}/stats", headers=h, timeout=10).status_code == 404

        r = requests.get(f"{TELEMETRY_URL}/telemetry/stats?instance_id={own_inst}", headers=h, timeout=10)
        assert r.status_code == 200 and len(r.json()) == 1
