"""allowed_address_pairs, router routes, network MTU and per-project networking quotas."""
import pytest
import requests
from fastapi.testclient import TestClient


@pytest.fixture
def api(networking, monkeypatch):
    from shared.auth_middleware import require_auth

    networking.app.dependency_overrides[require_auth] = lambda: {"project_id": "proj-a", "role": "member"}
    calls = []
    networking.ovn_cli.ovn = lambda *args, **kwargs: calls.append(args) or ""
    networking.calls = calls
    networking.limits = {}

    class Resp:
        def raise_for_status(self): pass
        def json(self): return networking.limits

    monkeypatch.setattr("requests.get", lambda *a, **k: Resp())
    networking.client = TestClient(networking.app)
    with networking.db.SessionLocal() as db:
        db.add(networking.models.Network(id="n1", name="net", project_id="proj-a"))
        db.add(networking.models.SecurityGroup(id="default", name="default", project_id="proj-a"))
        db.commit()
    return networking


def test_allowed_address_pairs_roundtrip_and_ovn_addresses(api):
    pairs = [{"ip_address": "10.0.0.50"}, {"ip_address": "10.0.0.60", "mac_address": "02:aa:bb:cc:dd:ee"}]
    res = api.client.post("/ports", json={"name": "p1", "network": "net", "fixed_ip": "10.0.0.5", "allowed_address_pairs": pairs})
    assert res.status_code == 201, res.text
    assert api.client.get("/ports/p1").json()["allowed_address_pairs"] == pairs
    addr = [c for c in api.calls if c[0] == "lsp-set-addresses"][-1][2:]
    assert addr[0].endswith("10.0.0.5 10.0.0.50") and addr[1] == "02:aa:bb:cc:dd:ee 10.0.0.60"


def test_allowed_address_pair_update_and_validation(api):
    api.client.post("/ports", json={"name": "p2", "network": "net"})
    assert api.client.patch("/ports/p2", json={"allowed_address_pairs": [{"ip_address": "not-an-ip"}]}).status_code == 422
    assert api.client.patch("/ports/p2", json={"allowed_address_pairs": [{"ip_address": "10.1.0.0/24"}]}).status_code == 200
    assert api.client.get("/ports/p2").json()["allowed_address_pairs"] == [{"ip_address": "10.1.0.0/24"}]


def test_router_routes_add_and_remove(api):
    api.client.post("/routers", json={"name": "r"})
    api.calls.clear()
    res = api.client.patch("/routers/r", json={"routes": [{"destination": "192.168.9.0/24", "nexthop": "10.0.0.9"}]})
    assert res.status_code == 200 and res.json()["routes"] == [{"destination": "192.168.9.0/24", "nexthop": "10.0.0.9"}]
    assert ("--may-exist", "lr-route-add", "r", "192.168.9.0/24", "10.0.0.9") in api.calls
    api.client.patch("/routers/r", json={"routes": []})
    assert ("--if-exists", "lr-route-del", "r", "192.168.9.0/24", "10.0.0.9") in api.calls
    assert api.client.get("/routers/r").json()["routes"] == []
    assert api.client.patch("/routers/r", json={"routes": [{"destination": "x", "nexthop": "y"}]}).status_code == 422


def test_network_mtu_default_and_custom(api):
    assert api.client.post("/networks", json={"name": "m1", "mtu": 1400}).status_code == 201
    assert api.client.get("/networks/m1").json()["mtu"] == 1400
    assert api.client.get("/networks/net").json()["mtu"] == 1442
    assert api.client.post("/networks", json={"name": "m2", "mtu": 10}).status_code == 422


def test_dhcp_options_carry_the_mtu(api):
    assert "options:mtu=1400" in api.ovn_cli.dhcp_options_args("10.0.0.0/24", "10.0.0.1", [], mtu=1400)
    assert not any(a.startswith("options:mtu") for a in api.ovn_cli.dhcp_options_args("10.0.0.0/24", "10.0.0.1", []))


def test_quota_blocks_the_next_create(api):
    api.limits = {"max_routers": 1}
    assert api.client.post("/routers", json={"name": "q1"}).status_code == 201
    res = api.client.post("/routers", json={"name": "q2"})
    assert res.status_code == 413 and "max_routers" in res.json()["detail"]


def test_quota_fails_open_when_auth_is_down(api, monkeypatch):
    def down(*a, **k):
        raise requests.ConnectionError("auth-api down")

    monkeypatch.setattr("requests.get", down)
    assert api.client.post("/routers", json={"name": "q3"}).status_code == 201
