import pytest
from fastapi.testclient import TestClient
from shared.auth_middleware import require_auth


@pytest.fixture
def client(networking, monkeypatch):
    networking.app.dependency_overrides[require_auth] = lambda: {"project_id": "proj-1", "role": "admin"}
    monkeypatch.setattr(networking.ovn_cli, "ovn", lambda *args, **kwargs: "")
    with networking.db.SessionLocal() as db:
        db.add_all([
            networking.models.Network(id="net-1", name="ds"),
            networking.models.Subnet(id="v4", name="v4", network_id="net-1", cidr="10.99.0.0/24", gateway="10.99.0.1", ip_version=4),
            networking.models.Subnet(id="v6", name="v6", network_id="net-1", cidr="fd42:7:7::/64", gateway="fd42:7:7::1", ip_version=6),
            networking.models.SecurityGroup(id="sg-default", name="default"),
        ])
        db.commit()
    return TestClient(networking.app, raise_server_exceptions=False)


def _port(client, name, **entry):
    return client.post("/ports", json={"name": name, "network": "ds", "fixed_ips": [{"subnet_id": "v4"}, {"subnet_id": "v6", **entry}]})


def test_dual_stack_ports_get_different_addresses_in_both_families(client):
    # this was the lab run: the first port worked, the second got the first one's IPv6 address and failed with 502
    first, second, third = (_port(client, f"p{i}") for i in range(3))
    assert {r.status_code for r in (first, second, third)} == {201}
    assert [r.json()["ipv6_address"] for r in (first, second, third)] == ["fd42:7:7::2", "fd42:7:7::3", "fd42:7:7::4"]
    assert [r.json()["fixed_ip"] for r in (first, second, third)] == ["10.99.0.2", "10.99.0.3", "10.99.0.4"]


def test_an_explicit_ipv6_address_is_checked(client):
    assert _port(client, "p1", ip_address="fd42:7:7::10").json()["ipv6_address"] == "fd42:7:7::10"
    taken = _port(client, "p2", ip_address="fd42:7:7::10")
    assert taken.status_code == 409 and "not available" in taken.json()["detail"]
    assert _port(client, "p3", ip_address="fd99::5").status_code == 400      # outside the subnet; it used to be accepted
    assert _port(client, "p4", ip_address="fd42:7:7::1").status_code == 409  # the gateway


def test_a_failed_port_leaves_nothing_behind_and_the_address_is_reusable(client, networking):
    assert _port(client, "p1", ip_address="fd99::5").status_code == 400
    with networking.db.SessionLocal() as db:
        assert db.query(networking.models.Port).count() == 0
    assert _port(client, "p2", ip_address="fd42:7:7::2").status_code == 201


def test_deleting_a_port_frees_its_ipv6_address(client):
    first = _port(client, "p1")
    assert client.delete(f"/ports/{first.json()['id']}").status_code in (200, 204)
    assert _port(client, "p2").json()["ipv6_address"] == "fd42:7:7::2"


def test_naming_only_the_ipv6_subnet_still_gives_an_ipv4_address(client):
    # what compute-api sends for `ipv6` on a create request: it does not look up the IPv4 subnet
    port = client.post("/ports", json={"name": "only6", "network": "ds", "fixed_ips": [{"subnet_id": "v6"}]})
    assert port.status_code == 201
    assert port.json()["fixed_ip"] == "10.99.0.2" and port.json()["ipv6_address"] == "fd42:7:7::2"
