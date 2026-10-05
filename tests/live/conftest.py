"""Fixtures for the live tests: they talk to a running CloudX lab (run them with `pytest --live`).

Credentials come from the environment (AUTH_ADMIN_PASSWORD, AUTH_SECRET_KEY: see /etc/cloudx/cloudx.env on the
controller); service URLs default to a lab reachable on localhost and can be overridden with <SERVICE>_API_URL.
"""
import os
import uuid

import pytest
import requests

AUTH_URL = os.getenv("AUTH_API_URL", "http://127.0.0.1:8005")
NETWORKING_URL = os.getenv("NETWORKING_API_URL", "http://127.0.0.1:8001")
ADMIN_USER = os.getenv("AUTH_ADMIN_USERNAME", "admin")
ADMIN_PASS = os.environ["AUTH_ADMIN_PASSWORD"]


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(f"{AUTH_URL}/auth/tokens", json={"username": ADMIN_USER, "password": ADMIN_PASS}, timeout=10)
    assert r.status_code in (200, 201), r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def auth_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"}


@pytest.fixture(scope="module")
def test_project_id(auth_headers):
    """A project for tests that need one: reuse it if it exists, create it otherwise."""
    r = requests.get(f"{AUTH_URL}/auth/projects", headers=auth_headers, timeout=10)
    if r.status_code == 200:
        for p in r.json().get("projects", []):
            if p.get("name") == "cloudx-live-tests":
                return p["id"]
    r = requests.post(f"{AUTH_URL}/auth/projects", headers=auth_headers, timeout=10,
                      json={"name": "cloudx-live-tests", "description": "Created by the live test suite"})
    if r.status_code in (200, 201):
        return r.json()["id"]
    return str(uuid.uuid4())


@pytest.fixture(scope="module")
def test_router(auth_headers):
    """A router for the VPC peering / BGP tests."""
    r = requests.post(f"{NETWORKING_URL}/routers", headers=auth_headers, timeout=10,
                      json={"name": f"test-router-{uuid.uuid4().hex[:6]}", "external_gateway_info": None})
    if r.status_code in (200, 201):
        return r.json()
    pytest.skip("Could not create test router")


@pytest.fixture(scope="session", autouse=True)
def _sweep_lab_leftovers():
    """The live tests create routers, networks, keypairs, fake resource providers, ...: remove them afterwards."""
    yield
    from tests.live.cleanup import sweep

    try:
        removed = sweep()
    except Exception as exc:  # cleaning up must never fail the run
        print(f"\nlive cleanup skipped: {exc}")
        return
    if removed:
        print(f"\nlive cleanup removed: {removed}")
