from __future__ import annotations

import os
import uuid

import pytest
import requests

EVENT_URL = os.getenv("EVENT_API_URL", "http://127.0.0.1:8012")
# the SSRF guard rejects non-public hosts unless EVENT_ALLOW_PRIVATE_WEBHOOKS=true on the server; this needs DNS
WEBHOOK = "https://example.com/hook"


def _create_sub(headers, **extra):
    body = {"name": f"t-{uuid.uuid4().hex[:6]}", "endpoint_url": WEBHOOK, "event_types": ["instance.created"], **extra}
    return requests.post(f"{EVENT_URL}/events/subscriptions", headers=headers, json=body, timeout=10)


class TestEventAPI:
    def test_health(self):
        r = requests.get(f"{EVENT_URL}/health", timeout=10)
        assert r.status_code in (200, 201)
        assert r.json()["service"] == "event-api"

    def test_event_types(self):
        r = requests.get(f"{EVENT_URL}/events/types", timeout=10)
        assert r.status_code == 200
        names = [e["type"] for e in r.json()["event_types"]]
        assert "instance.created" in names
        assert "instance.crashed" in names

    def test_subscription_secret_only_on_create(self, auth_headers):
        r = _create_sub(auth_headers, secret="s3cret-value")
        assert r.status_code == 201, r.text
        sub = r.json()
        sid = sub["id"]
        try:
            assert sub["secret"] == "s3cret-value"
            assert sub["has_secret"] is True

            got = requests.get(f"{EVENT_URL}/events/subscriptions/{sid}", headers=auth_headers, timeout=10).json()
            assert "secret" not in got
            assert got["has_secret"] is True

            listed = requests.get(f"{EVENT_URL}/events/subscriptions", headers=auth_headers, timeout=10).json()
            row = next(s for s in listed if s["id"] == sid)
            assert "secret" not in row and row["has_secret"] is True

            # admin-only internal endpoint (used by the event worker) still returns it
            r = requests.get(f"{EVENT_URL}/events/internal/subscriptions/{sid}", headers=auth_headers, timeout=10)
            assert r.status_code == 200 and r.json()["secret"] == "s3cret-value"
        finally:
            requests.delete(f"{EVENT_URL}/events/subscriptions/{sid}", headers=auth_headers, timeout=10)

    def test_subscription_without_secret(self, auth_headers):
        r = _create_sub(auth_headers)
        assert r.status_code == 201
        try:
            assert r.json()["has_secret"] is False
        finally:
            requests.delete(f"{EVENT_URL}/events/subscriptions/{r.json()['id']}", headers=auth_headers, timeout=10)

    def test_ssrf_guard_rejects_private_urls(self, auth_headers):
        r = _create_sub(auth_headers, endpoint_url="http://127.0.0.1:8000/x")
        if r.status_code == 201:  # server runs with EVENT_ALLOW_PRIVATE_WEBHOOKS=true
            requests.delete(f"{EVENT_URL}/events/subscriptions/{r.json()['id']}", headers=auth_headers, timeout=10)
            pytest.skip("EVENT_ALLOW_PRIVATE_WEBHOOKS is enabled on this lab")
        assert r.status_code == 422

    def test_emit_and_log_list(self, auth_headers, test_project_id):
        rid = str(uuid.uuid4())
        r = requests.post(f"{EVENT_URL}/events/emit", headers=auth_headers, timeout=10, json={
            "event_type": "instance.created", "resource_id": rid, "project_id": test_project_id,
            "payload": {"name": "test-vm"}})
        assert r.status_code == 201
        r = requests.get(f"{EVENT_URL}/events/logs?resource_id={rid}&project_id={test_project_id}", headers=auth_headers, timeout=10)
        assert r.status_code == 200
        assert [e["resource_id"] for e in r.json()] == [rid]

    def test_deliver_event_creates_delivery_with_new_fields(self, auth_headers, test_project_id):
        # subscriptions are bound to the token's project, so the delivery is created for the admin's project
        r = _create_sub(auth_headers)
        assert r.status_code == 201
        sid = r.json()["id"]
        try:
            me = requests.get(f"{EVENT_URL}/events/subscriptions/{sid}", headers=auth_headers, timeout=10).json()
            r = requests.post(f"{EVENT_URL}/events/deliver", headers=auth_headers, timeout=10, json={
                "event_type": "instance.created", "project_id": me["project_id"], "payload": {"name": "vm"}})
            assert r.status_code == 202
            assert "event_id" in r.json()

            r = requests.get(f"{EVENT_URL}/events/deliveries?subscription_id={sid}", headers=auth_headers, timeout=10)
            assert r.status_code == 200
            assert r.json(), "consumer/emit did not create a delivery"
            d = r.json()[0]
            for k in ("event_type", "payload", "attempt_count", "next_retry_at", "last_error", "status"):
                assert k in d
            assert d["event_type"] == "instance.created"
            assert d["status"] in ("pending", "success", "failed")

            # retry resets state
            r = requests.post(f"{EVENT_URL}/events/deliveries/{d['id']}/retry", headers=auth_headers, timeout=10)
            assert r.status_code == 200
            assert r.json()["status"] == "pending" and r.json()["attempt_count"] == 0 and r.json()["next_retry_at"] is None

            # PATCH is admin-only and validates status
            url = f"{EVENT_URL}/events/deliveries/{d['id']}"
            assert requests.patch(url, headers=auth_headers, json={"status": "bogus"}, timeout=10).status_code == 422
            r = requests.patch(url, headers=auth_headers, json={"status": "failed", "last_error": "boom"}, timeout=10)
            assert r.status_code == 200
            assert r.json()["status"] == "failed" and r.json()["last_error"] == "boom"
        finally:
            requests.delete(f"{EVENT_URL}/events/subscriptions/{sid}", headers=auth_headers, timeout=10)

    def test_list_deliveries(self, auth_headers):
        r = requests.get(f"{EVENT_URL}/events/deliveries?limit=10", headers=auth_headers, timeout=10)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_admin_only_endpoints(self, auth_headers, member):
        h = member["headers"]
        assert requests.patch(f"{EVENT_URL}/events/deliveries/1", headers=h, json={"status": "failed"}, timeout=10).status_code == 403
        assert requests.get(f"{EVENT_URL}/events/internal/subscriptions/1", headers=h, timeout=10).status_code == 403

    def test_non_admin_scoped_to_own_project(self, auth_headers, member, test_project_id):
        admin_sub = _create_sub(auth_headers)
        assert admin_sub.status_code == 201
        admin_sid, admin_pid = admin_sub.json()["id"], admin_sub.json()["project_id"]
        h = member["headers"]
        try:
            assert admin_pid != member["project_id"]
            requests.post(f"{EVENT_URL}/events/emit", headers=auth_headers, timeout=10,
                          json={"event_type": "instance.created", "project_id": admin_pid})

            # other project's subscription is invisible / untouchable
            assert requests.get(f"{EVENT_URL}/events/subscriptions/{admin_sid}", headers=h, timeout=10).status_code == 404
            assert requests.delete(f"{EVENT_URL}/events/subscriptions/{admin_sid}", headers=h, timeout=10).status_code == 404
            r = requests.get(f"{EVENT_URL}/events/subscriptions?project_id={admin_pid}", headers=h, timeout=10)
            assert r.status_code == 200 and all(s["project_id"] == member["project_id"] for s in r.json())

            # logs: ?project_id ignored for non-admin
            r = requests.get(f"{EVENT_URL}/events/logs?project_id={admin_pid}", headers=h, timeout=10)
            assert r.status_code == 200 and all(e["project_id"] == member["project_id"] for e in r.json())

            # deliveries of the other project are invisible, and retry on them 404s
            r = requests.get(f"{EVENT_URL}/events/deliveries?subscription_id={admin_sid}", headers=h, timeout=10)
            assert r.status_code == 200 and r.json() == []
            mine = requests.get(f"{EVENT_URL}/events/deliveries?subscription_id={admin_sid}", headers=auth_headers, timeout=10).json()
            if mine:
                assert requests.post(f"{EVENT_URL}/events/deliveries/{mine[0]['id']}/retry", headers=h, timeout=10).status_code == 404
        finally:
            requests.delete(f"{EVENT_URL}/events/subscriptions/{admin_sid}", headers=auth_headers, timeout=10)
