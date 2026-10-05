from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import requests

BILLING_URL = os.getenv("BILLING_API_URL", "http://127.0.0.1:8011")

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
