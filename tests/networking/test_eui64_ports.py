"""On a slaac or dhcpv6-stateless subnet the guest builds an EUI-64 address from its MAC whatever CloudX allocated, so
CloudX allocates that one: the port, the server record, the OVN addresses and the guest agree on a single address."""
import ipaddress

import pytest
from fastapi.testclient import TestClient
from shared.auth_middleware import require_auth


@pytest.fixture
def lab(networking, monkeypatch):
    networking.app.dependency_overrides[require_auth] = lambda: {"project_id": "proj-1", "role": "admin"}
    calls = []
    monkeypatch.setattr(networking.ovn_cli, "ovn", lambda *a, **k: calls.append((a, k)) or "")
    with networking.db.SessionLocal() as db:
        db.add_all([networking.models.SecurityGroup(id="sg-default", name="default")] +
                   [networking.models.Network(id=f"n-{n}", name=n) for n in ("auto", "stateful", "plain", "dual")])
        db.commit()
    return networking, TestClient(networking.app, raise_server_exceptions=False), calls


def _subnet(client, name, network, cidr, **extra):
    body = {"name": name, "network": network, "cidr": cidr, "ip_version": 6 if ":" in cidr else 4, **extra}
    return client.post("/subnets", json=body)


def _port(client, name, network, **extra):
    return client.post("/ports", json={"name": name, "network": network, **extra})


def _eui64(cidr, mac):
    octets = [int(x, 16) for x in mac.split(":")]
    iid = bytes([octets[0] ^ 2, *octets[1:3], 0xFF, 0xFE, *octets[3:]])
    return str(ipaddress.ip_address(int(ipaddress.ip_network(cidr).network_address) | int.from_bytes(iid, "big")))


@pytest.mark.parametrize("mode", ["slaac", "dhcpv6-stateless"])
def test_a_port_gets_the_eui64_address_of_its_own_mac(lab, mode):
    networking, client, calls = lab
    assert _subnet(client, "s6", "auto", "fd42:9:1::/64", gateway_ip="fd42:9:1::1", ipv6_ra_mode=mode, ipv6_address_mode=mode).status_code == 201
    port = _port(client, "p1", "auto").json()
    expected = _eui64("fd42:9:1::/64", port["mac_address"])
    assert port["ipv6_address"] == expected
    assert port["fixed_ips"] == [{"ip_address": expected, "ip_version": 6}]
    assert ((("lsp-set-addresses", "p1", f"{port['mac_address']} {expected}"), {})) in calls   # OVN knows the same address


def test_two_ports_get_two_different_addresses(lab):
    _, client, _ = lab
    _subnet(client, "s6", "auto", "fd42:9:1::/64", gateway_ip="fd42:9:1::1", ipv6_address_mode="slaac")
    assert _port(client, "p1", "auto").json()["ipv6_address"] != _port(client, "p2", "auto").json()["ipv6_address"]


@pytest.mark.parametrize("extra", [{"ipv6_address_mode": "dhcpv6-stateful"}, {"ipv6_ra_mode": "slaac"}, {}])
def test_other_modes_keep_counting_up(lab, extra):
    _, client, _ = lab
    _subnet(client, "s6", "stateful", "fd42:9:2::/64", gateway_ip="fd42:9:2::1", **extra)
    assert [_port(client, f"p{i}", "stateful").json()["ipv6_address"] for i in (1, 2)] == ["fd42:9:2::2", "fd42:9:2::3"]


def test_an_address_named_in_fixed_ips_is_kept(lab):
    _, client, _ = lab
    sub = _subnet(client, "s6", "auto", "fd42:9:1::/64", gateway_ip="fd42:9:1::1", ipv6_address_mode="slaac").json()
    port = _port(client, "p1", "auto", fixed_ips=[{"subnet_id": sub["id"], "ip_address": "fd42:9:1::50"}]).json()
    assert port["ipv6_address"] == "fd42:9:1::50"


def test_a_dual_stack_port_counts_up_in_ipv4_and_uses_eui64_in_ipv6(lab):
    _, client, _ = lab
    v4 = _subnet(client, "s4", "dual", "10.99.0.0/24", gateway_ip="10.99.0.1").json()
    v6 = _subnet(client, "s6", "dual", "fd42:9:3::/64", gateway_ip="fd42:9:3::1", ipv6_address_mode="slaac").json()
    port = _port(client, "p1", "dual", fixed_ips=[{"subnet_id": v4["id"]}, {"subnet_id": v6["id"]}]).json()
    assert port["fixed_ip"] == "10.99.0.2"
    assert port["ipv6_address"] == _eui64("fd42:9:3::/64", port["mac_address"])


@pytest.mark.parametrize("mode", ["slaac", "dhcpv6-stateless"])
def test_such_a_subnet_must_be_a_slash_64(lab, mode):
    _, client, _ = lab
    refused = _subnet(client, "s6", "auto", "fd42:9:4::/80", gateway_ip="fd42:9:4::1", ipv6_address_mode=mode)
    assert refused.status_code == 400 and "/64" in refused.json()["detail"]


def test_a_stateful_subnet_may_be_any_size_and_a_slash_64_slaac_subnet_is_fine(lab):
    _, client, _ = lab
    assert _subnet(client, "a", "stateful", "fd42:9:5::/80", gateway_ip="fd42:9:5::1", ipv6_address_mode="dhcpv6-stateful").status_code == 201
    assert _subnet(client, "b", "auto", "fd42:9:6::/64", gateway_ip="fd42:9:6::1", ipv6_address_mode="slaac").status_code == 201


def test_an_empty_address_in_fixed_ips_counts_as_none_asked(lab):
    _, client, _ = lab
    sub = _subnet(client, "s6", "auto", "fd42:9:1::/64", gateway_ip="fd42:9:1::1", ipv6_address_mode="slaac").json()
    port = _port(client, "p1", "auto", fixed_ips=[{"subnet_id": sub["id"], "ip_address": ""}]).json()
    assert port["ipv6_address"] == _eui64("fd42:9:1::/64", port["mac_address"])
