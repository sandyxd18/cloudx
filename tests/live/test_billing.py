from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import requests

BILLING_URL = os.getenv("BILLING_API_URL", "http://127.0.0.1:8011")


def _usage(project_id, rtype="vcpu", qty="24.0000", start=None, end=None, rid=None):
    now = datetime.now(timezone.utc)
    return {
        "project_id": project_id,
        "resource_type": rtype,
        "resource_id": rid or str(uuid.uuid4()),
        "quantity": qty,
        "period_start": (start or now - timedelta(hours=24)).isoformat(),
        "period_end": (end or now).isoformat(),
    }


class TestBillingAPI:
    def test_health(self):
        r = requests.get(f"{BILLING_URL}/health", timeout=10)
        assert r.status_code in (200, 201)
        assert r.json()["service"] == "billing-api"

    def test_rating_sheets(self, auth_headers):
        r = requests.get(f"{BILLING_URL}/billing/rating-sheets", headers=auth_headers, timeout=10)
        assert r.status_code == 200
        types = [s["resource_type"] for s in r.json()]
        assert "vcpu" in types
        assert "ram" in types

    def test_rating_sheet_create_and_partial_patch(self, auth_headers):
        rtype = f"gpu-{uuid.uuid4().hex[:6]}"
        r = requests.post(f"{BILLING_URL}/billing/rating-sheets", headers=auth_headers, timeout=10, json={
            "resource_type": rtype, "unit": "gpu-hour", "rate_per_unit": "500.0000", "currency": "IDR", "description": "GPU"})
        assert r.status_code == 201
        sid = r.json()["id"]
        assert float(r.json()["rate_per_unit"]) == 500.0

        r = requests.patch(f"{BILLING_URL}/billing/rating-sheets/{sid}", headers=auth_headers, json={"rate_per_unit": "600"}, timeout=10)
        assert r.status_code == 200
        assert float(r.json()["rate_per_unit"]) == 600.0
        assert r.json()["unit"] == "gpu-hour" and r.json()["description"] == "GPU"  # untouched fields stay

        r = requests.get(f"{BILLING_URL}/billing/rating-sheets/{sid}", headers=auth_headers, timeout=10)
        assert r.status_code == 200 and float(r.json()["rate_per_unit"]) == 600.0
        # deactivate so it does not linger in the active list
        requests.patch(f"{BILLING_URL}/billing/rating-sheets/{sid}", headers=auth_headers, json={"is_active": False}, timeout=10)

    def test_create_usage_record(self, auth_headers, test_project_id):
        body = _usage(test_project_id)
        body.update(unit="vcpu-hour", rate_per_unit="100.0000")
        r = requests.post(f"{BILLING_URL}/billing/usage-records", headers=auth_headers, json=body, timeout=10)
        assert r.status_code == 201
        assert r.json()["project_id"] == test_project_id
        assert float(r.json()["total_cost"]) == 2400.0

    def test_usage_batch_admin_only_and_dedupes(self, auth_headers, member):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        item = _usage(member["project_id"], "vcpu", "2", now - timedelta(hours=2), now - timedelta(hours=1))
        r = requests.post(f"{BILLING_URL}/billing/usage-records/batch", headers=member["headers"], json=[item], timeout=10)
        assert r.status_code == 403

        bad = _usage(member["project_id"], "no-such-type")
        r = requests.post(f"{BILLING_URL}/billing/usage-records/batch", headers=auth_headers, json=[item, bad], timeout=10)
        assert r.status_code == 200
        assert r.json()["created"] == 1
        assert [s["reason"] for s in r.json()["skipped"]] == ["no active rating sheet"]

        r = requests.post(f"{BILLING_URL}/billing/usage-records/batch", headers=auth_headers, json=[item], timeout=10)
        assert r.json()["created"] == 0
        assert r.json()["skipped"][0]["reason"] == "duplicate"

    def test_cleanup_admin_only(self, auth_headers, member):
        assert requests.post(f"{BILLING_URL}/billing/cleanup?days=36500", headers=member["headers"], timeout=10).status_code == 403
        r = requests.post(f"{BILLING_URL}/billing/cleanup?days=36500", headers=auth_headers, timeout=10)
        assert r.status_code == 200
        assert "deleted" in r.json()

    def test_billing_summary_by_resource(self, auth_headers, test_project_id):
        r = requests.get(f"{BILLING_URL}/billing/summary?project_id={test_project_id}", headers=auth_headers, timeout=10)
        assert r.status_code == 200
        body = r.json()
        assert body["project_id"] == test_project_id
        assert "total_cost" in body
        assert isinstance(body["by_resource"], list)
        for row in body["by_resource"]:
            assert {"resource_type", "quantity", "cost"} <= set(row)

    def test_invoice_query_params_and_duplicate_409(self, auth_headers, test_project_id):
        # past month so the period is stable and the usage record sits inside it
        first = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        start = (first - timedelta(days=1)).replace(day=1)
        year, month = start.year, start.month
        # unique project per run would be cleaner; a repeat run hits 409 on create, which we accept below
        item = _usage(test_project_id, "ram", "48", start + timedelta(days=1), start + timedelta(days=2))
        requests.post(f"{BILLING_URL}/billing/usage-records/batch", headers=auth_headers, json=[item], timeout=10)

        url = f"{BILLING_URL}/billing/invoices?project_id={test_project_id}&year={year}&month={month}"
        r = requests.post(url, headers=auth_headers, timeout=10)
        assert r.status_code in (201, 409), r.text
        if r.status_code == 201:
            inv = r.json()
            assert inv["project_id"] == test_project_id and inv["status"] == "draft"
            assert isinstance(inv["items"], list)
            assert requests.post(url, headers=auth_headers, timeout=10).status_code == 409
        else:
            inv = next(i for i in requests.get(
                f"{BILLING_URL}/billing/invoices?project_id={test_project_id}", headers=auth_headers, timeout=10).json()
                if i["period_start"].startswith(start.strftime("%Y-%m")))
        r = requests.get(f"{BILLING_URL}/billing/invoices/{inv['id']}", headers=auth_headers, timeout=10)
        assert r.status_code == 200
        assert "items" in r.json()

    def test_invalid_month_rejected(self, auth_headers, test_project_id):
        r = requests.post(f"{BILLING_URL}/billing/invoices?project_id={test_project_id}&year=2025&month=13", headers=auth_headers, timeout=10)
        assert r.status_code == 422

    def test_list_invoices(self, auth_headers, test_project_id):
        r = requests.get(f"{BILLING_URL}/billing/invoices?project_id={test_project_id}", headers=auth_headers, timeout=10)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_non_admin_scoped_to_own_project(self, auth_headers, member, test_project_id):
        h = member["headers"]
        # an invoice and usage exist for the other project; member must not see them
        now = datetime.now(timezone.utc)
        requests.post(f"{BILLING_URL}/billing/usage-records/batch", headers=auth_headers, timeout=10,
                      json=[_usage(test_project_id, "vcpu", "1", now - timedelta(hours=5), now - timedelta(hours=4))])
        r = requests.get(f"{BILLING_URL}/billing/usage-records?project_id={test_project_id}", headers=h, timeout=10)
        assert r.status_code == 200
        assert all(u["project_id"] == member["project_id"] for u in r.json())
        r = requests.get(f"{BILLING_URL}/billing/invoices?project_id={test_project_id}", headers=h, timeout=10)
        assert r.status_code == 200
        assert all(i["project_id"] == member["project_id"] for i in r.json())
        r = requests.get(f"{BILLING_URL}/billing/summary?project_id={test_project_id}", headers=h, timeout=10)
        assert r.status_code == 200 and r.json()["project_id"] == member["project_id"]

        other = requests.get(f"{BILLING_URL}/billing/invoices?project_id={test_project_id}&limit=1", headers=auth_headers, timeout=10).json()
        if other:
            iid = other[0]["id"]
            assert requests.get(f"{BILLING_URL}/billing/invoices/{iid}", headers=h, timeout=10).status_code == 404
            assert requests.post(f"{BILLING_URL}/billing/invoices/{iid}/pdf", headers=h, timeout=30).status_code == 404

    def test_rating_sheet_write_admin_only(self, member):
        r = requests.post(f"{BILLING_URL}/billing/rating-sheets", headers=member["headers"], timeout=10,
                          json={"resource_type": "x", "unit": "h", "rate_per_unit": "1"})
        assert r.status_code == 403
