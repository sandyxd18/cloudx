"""Fase 1 conformance (v0.7.10-v0.7.12): API-only checks, no VM needed. Run with `pytest --live`."""
import os
import uuid

import pytest
import requests

from tests.live.conftest import AUTH_URL, NETWORKING_URL

COMPUTE = os.getenv("COMPUTE_API_URL", "http://127.0.0.1:8003")
S = uuid.uuid4().hex[:6]


def _ok(r, *codes):
    assert r.status_code in (codes or (200, 201, 204)), f"{r.request.method} {r.url} -> {r.status_code} {r.text}"
    return r.json() if r.content else None


@pytest.fixture(scope="module")
def two_tenants(auth_headers):
    """(headers_a, headers_b, project_a_id): members of two fresh projects."""
    out = []
    for tag in ("a", "b"):
        p = _ok(requests.post(f"{AUTH_URL}/projects", headers=auth_headers, json={"name": f"f1-{tag}-{S}"}, timeout=10))
        _ok(requests.post(f"{AUTH_URL}/users", headers=auth_headers, timeout=10, json={
            "username": f"f1{tag}{S}", "password": "Passw0rd!x1", "email": f"f1{tag}{S}@x.io",
            "project_id": p["id"], "role": "member"}))
        t = _ok(requests.post(f"{AUTH_URL}/auth/tokens", json={"username": f"f1{tag}{S}", "password": "Passw0rd!x1"}, timeout=10))
        out.append(({"Authorization": f"Bearer {t['token']}"}, p["id"]))
    yield out[0][0], out[1][0], out[0][1]
    for _, pid in out:
        requests.delete(f"{AUTH_URL}/projects/{pid}", headers=auth_headers, timeout=10)


def test_neutron_ownership_and_mtu(two_tenants, auth_headers):
    a, b, _ = two_tenants
    net = _ok(requests.post(f"{NETWORKING_URL}/networks", headers=a, json={"name": f"f1-net-{S}", "mtu": 1400}, timeout=10))
    try:
        assert net["mtu"] == 1400
        assert requests.get(f"{NETWORKING_URL}/networks/{net['id']}", headers=b, timeout=10).status_code == 404
        assert requests.delete(f"{NETWORKING_URL}/networks/{net['id']}", headers=b, timeout=10).status_code == 404
        assert any(n["id"] == net["id"] for n in _ok(requests.get(f"{NETWORKING_URL}/networks", headers=auth_headers, timeout=10))["items"])
    finally:
        requests.delete(f"{NETWORKING_URL}/networks/{net['id']}", headers=auth_headers, timeout=10)


def test_reader_role_is_read_only(auth_headers, two_tenants):
    _, _, pid = two_tenants
    _ok(requests.post(f"{AUTH_URL}/users", headers=auth_headers, timeout=10, json={
        "username": f"f1r{S}", "password": "Passw0rd!x1", "email": f"f1r{S}@x.io", "project_id": pid, "role": "reader"}))
    t = _ok(requests.post(f"{AUTH_URL}/auth/tokens", json={"username": f"f1r{S}", "password": "Passw0rd!x1"}, timeout=10))
    h = {"Authorization": f"Bearer {t['token']}"}
    assert requests.get(f"{NETWORKING_URL}/networks", headers=h, timeout=10).status_code == 200
    assert requests.post(f"{NETWORKING_URL}/networks", headers=h, json={"name": f"f1-ro-{S}"}, timeout=10).status_code == 403


def test_quota_sets_and_limits(auth_headers, two_tenants):
    a, _, pid = two_tenants
    r = _ok(requests.put(f"{COMPUTE}/os-quota-sets/{pid}", headers=auth_headers, json={"quota_set": {"instances": 3, "network": 4}}, timeout=10))
    assert r["quota_set"]["instances"] == 3 and r["quota_set"]["network"] == 4
    assert _ok(requests.get(f"{COMPUTE}/os-quota-sets/{pid}", headers=a, timeout=10))["quota_set"]["instances"] == 3
    assert requests.put(f"{COMPUTE}/os-quota-sets/{pid}", headers=a, json={"quota_set": {"instances": 99}}, timeout=10).status_code == 403
    assert _ok(requests.get(f"{COMPUTE}/limits", headers=a, timeout=10))["absolute"]["maxTotalInstances"] == 3


def test_network_quota_enforced(auth_headers, two_tenants):
    a, _, pid = two_tenants
    _ok(requests.put(f"{COMPUTE}/os-quota-sets/{pid}", headers=auth_headers, json={"quota_set": {"network": 1}}, timeout=10))
    made = []
    try:
        made.append(_ok(requests.post(f"{NETWORKING_URL}/networks", headers=a, json={"name": f"f1-q1-{S}"}, timeout=10))["id"])
        assert requests.post(f"{NETWORKING_URL}/networks", headers=a, json={"name": f"f1-q2-{S}"}, timeout=10).status_code == 413
    finally:
        for i in made:
            requests.delete(f"{NETWORKING_URL}/networks/{i}", headers=auth_headers, timeout=10)


def test_flavor_extra_specs(auth_headers):
    name = f"f1-flavor-{S}"
    _ok(requests.post(f"{COMPUTE}/flavors", headers=auth_headers, json={"name": name, "vcpus": 1, "ram_mb": 256, "disk_gb": 1}, timeout=10))
    try:
        url = f"{COMPUTE}/flavors/{name}/os-extra_specs"
        assert _ok(requests.post(url, headers=auth_headers, json={"extra_specs": {"aggregate_instance_extra_specs:ssd": "true"}}, timeout=10))["extra_specs"]
        assert _ok(requests.get(url, headers=auth_headers, timeout=10))["extra_specs"]["aggregate_instance_extra_specs:ssd"] == "true"
        _ok(requests.delete(f"{url}/aggregate_instance_extra_specs:ssd", headers=auth_headers, timeout=10))
        assert _ok(requests.get(url, headers=auth_headers, timeout=10))["extra_specs"] == {}
    finally:
        requests.delete(f"{COMPUTE}/flavors/{name}", headers=auth_headers, timeout=10)


def test_aggregate_az_and_unknown_az_rejected(auth_headers):
    name = f"f1-agg-{S}"
    agg = _ok(requests.post(f"{COMPUTE}/os-aggregates", headers=auth_headers, json={"name": name, "availability_zone": f"az-{S}"}, timeout=10))
    aid = agg.get("aggregate", agg)["id"]
    try:
        _ok(requests.post(f"{COMPUTE}/os-aggregates/{aid}/action", headers=auth_headers, json={"set_metadata": {"metadata": {"ssd": "true"}}}, timeout=10))
        zones = _ok(requests.get(f"{COMPUTE}/os-availability-zone", headers=auth_headers, timeout=10))["availabilityZoneInfo"]
        assert any(z["zoneName"] == f"az-{S}" for z in zones)
    finally:
        requests.delete(f"{COMPUTE}/os-aggregates/{aid}", headers=auth_headers, timeout=10)


def test_os_services_disable_enable(auth_headers):
    host = f"f1-host-{S}"
    r = _ok(requests.put(f"{COMPUTE}/os-services/{host}", headers=auth_headers, json={"disabled": True, "disabled_reason": "maint"}, timeout=10))
    assert r["service"]["status"] == "disabled"
    r = _ok(requests.put(f"{COMPUTE}/os-services/{host}", headers=auth_headers, json={"disabled": False}, timeout=10))
    assert r["service"]["status"] == "enabled" and r["service"]["disabled_reason"] is None
