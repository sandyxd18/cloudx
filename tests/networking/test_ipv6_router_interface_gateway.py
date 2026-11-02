"""An IPv6 subnet may be created without a gateway (hosts learn routers from advertisements). A router interface needs an
address on the subnet, and the gateway is that address: without one the address was just "/64" and ovn-nbctl refused it
("invalid network address: /64"), which came back as HTTP 502."""
import pytest
from fastapi.testclient import TestClient
from shared.auth_middleware import require_auth
from sqlalchemy import select


@pytest.fixture
def lab(networking, monkeypatch):
    networking.app.dependency_overrides[require_auth] = lambda: {"project_id": "proj-1", "role": "admin"}
    calls = []

    def fake_ovn(*args, **kwargs):
        calls.append((args, kwargs))
        return "dhcp-uuid-1" if args[:2] == ("create", "DHCP_Options") else ""

    monkeypatch.setattr(networking.ovn_cli, "ovn", fake_ovn)
    with networking.db.SessionLocal() as db:
        db.add_all([
            networking.models.SecurityGroup(id="sg-default", name="default"),
            networking.models.Network(id="n6", name="v6net"),
        ])
        db.commit()
    client = TestClient(networking.app, raise_server_exceptions=False)
    client.post("/routers", json={"name": "r"})
    return networking, client, calls


def _v6_subnet(client, name, cidr, gateway=None):
    res = client.post("/subnets", json={"name": name, "network": "v6net", "cidr": cidr, "gateway_ip": gateway, "ip_version": 6})
    assert res.status_code == 201, res.text
    return res.json()["id"]


def test_an_interface_on_an_ipv6_subnet_without_a_gateway_is_refused_with_400(lab):
    _, client, _ = lab
    sub = _v6_subnet(client, "nogw", "fd42:9::/64")
    res = client.post("/routers/r/interfaces", json={"subnet": sub})
    assert res.status_code == 400
    assert "gateway" in res.json()["detail"]


def test_the_refusal_comes_before_anything_reaches_ovn_or_the_database(lab):
    networking, client, calls = lab
    sub = _v6_subnet(client, "nogw", "fd42:9::/64")
    calls.clear()
    client.post("/routers/r/interfaces", json={"subnet": sub})
    assert calls == []
    with networking.db.SessionLocal() as db:
        assert db.scalars(select(networking.models.RouterInterface)).all() == []


def test_an_ipv6_subnet_with_a_gateway_still_gets_its_interface(lab):
    _, client, calls = lab
    sub = _v6_subnet(client, "gw", "fd42:9:1::/64", "fd42:9:1::1")
    res = client.post("/routers/r/interfaces", json={"subnet": sub})
    assert res.status_code == 201, res.text
    assert any(a[:2] == ("--may-exist", "lrp-add") and a[-1] == "fd42:9:1::1/64" for a, _ in calls)
