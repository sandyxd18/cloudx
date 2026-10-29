"""A network with only an IPv6 subnet used to give its ports no address at all: create_port looked for an IPv4 subnet
and, finding none, left fixed_ip empty. Now a port gets one address from each family the network has a subnet for."""
import pytest
from fastapi.testclient import TestClient
from shared.auth_middleware import require_auth


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
            networking.models.Network(id="n6", name="v6only"),
            networking.models.Network(id="n46", name="dual"),
            networking.models.Network(id="n4", name="v4only"),
        ])
        db.commit()
    client = TestClient(networking.app, raise_server_exceptions=False)
    return networking, client, calls


def _subnet(client, name, network, cidr, gateway=None, **extra):
    body = {"name": name, "network": network, "cidr": cidr, "ip_version": 6 if ":" in cidr else 4, **extra}
    if gateway:
        body["gateway_ip"] = gateway
    return client.post("/subnets", json=body)


def _port(client, name, network, **extra):
    return client.post("/ports", json={"name": name, "network": network, **extra})


def test_a_port_on_an_ipv6_only_network_gets_an_ipv6_address(lab):
    _, client, _ = lab
    v6 = _subnet(client, "s6", "v6only", "fd42:7:7::/64", "fd42:7:7::1").json()
    first, second = _port(client, "p1", "v6only"), _port(client, "p2", "v6only")
    assert (first.status_code, second.status_code) == (201, 201)
    a, b = first.json(), second.json()
    assert (a["ipv6_address"], b["ipv6_address"]) == ("fd42:7:7::2", "fd42:7:7::3")
    assert a["fixed_ip"] is None and a["ip_address"] is None
    assert a["fixed_ips"] == [{"ip_address": "fd42:7:7::2", "ip_version": 6}]
    assert a["subnet_id"] == v6["id"]            # keeps the subnet from being deleted under the port
    assert (a["cidr"], a["gateway_ip"]) == (None, None)  # those two stay the IPv4 view
    assert (a["ipv6_cidr"], a["ipv6_gateway_ip"]) == ("fd42:7:7::/64", "fd42:7:7::1")


def test_a_dual_stack_network_gives_both_families_without_being_asked(lab):
    _, client, _ = lab
    _subnet(client, "s4", "dual", "10.99.0.0/24", "10.99.0.1")
    _subnet(client, "s6", "dual", "fd42:8:8::/64", "fd42:8:8::1")
    port = _port(client, "p1", "dual").json()
    assert (port["fixed_ip"], port["ipv6_address"]) == ("10.99.0.2", "fd42:8:8::2")
    assert (port["cidr"], port["ipv6_cidr"]) == ("10.99.0.0/24", "fd42:8:8::/64")


def test_an_ipv4_only_network_is_unchanged(lab):
    _, client, _ = lab
    _subnet(client, "s4", "v4only", "10.98.0.0/24", "10.98.0.1")
    port = _port(client, "p1", "v4only").json()
    assert (port["fixed_ip"], port["ipv6_address"], port["ipv6_cidr"]) == ("10.98.0.2", None, None)


def test_asking_for_an_address_turns_the_automatic_ipv6_off(lab):
    # the callers that pick their own IPv4 (a router interface, a VIP) should not suddenly hold an IPv6 address too
    _, client, _ = lab
    v4 = _subnet(client, "s4", "dual", "10.99.0.0/24", "10.99.0.1").json()
    _subnet(client, "s6", "dual", "fd42:8:8::/64", "fd42:8:8::1")
    assert _port(client, "p1", "dual", fixed_ip="10.99.0.50").json()["ipv6_address"] is None
    assert _port(client, "p2", "dual", fixed_ips=[{"subnet_id": v4["id"]}]).json()["ipv6_address"] is None


def test_an_ipv6_subnet_does_not_need_a_gateway_but_an_ipv4_one_does(lab):
    _, client, _ = lab
    assert _subnet(client, "s6", "v6only", "fd42:7:7::/64", ip_version=6).status_code == 201
    refused = _subnet(client, "s4", "v4only", "10.98.0.0/24")
    assert refused.status_code == 400 and "gateway" in refused.json()["detail"]


def test_a_gateway_less_ipv6_subnet_still_allocates_from_the_first_host(lab):
    _, client, _ = lab
    _subnet(client, "s6", "v6only", "fd42:7:7::/64", ip_version=6)
    assert _port(client, "p1", "v6only").json()["ipv6_address"] == "fd42:7:7::1"


def _created_options(calls):
    return [args for args, _ in calls if args[:2] == ("create", "DHCP_Options")]


def test_dhcpv6_options_use_a_mac_as_server_id(lab):
    # `server_id` of a DHCPv6 row is an Ethernet address; the gateway (an IPv6 address) made OVN refuse the row, which
    # was swallowed, so dhcp_options_id stayed empty
    _, client, calls = lab
    r = _subnet(client, "s6", "v6only", "fd42:7:7::/64", "fd42:7:7::1", ip_version=6, ipv6_ra_mode="dhcpv6-stateful", ipv6_address_mode="dhcpv6-stateful")
    (args,) = _created_options(calls)
    server_id = next(a for a in args if a.startswith("options:server_id=")).split("=", 1)[1]
    assert len(server_id.split(":")) == 6 and server_id != "fd42:7:7::1"
    assert "cidr=fd42:7:7::/64" in args and "options:dhcpv6_stateless=true" not in args
    assert r.json()["dhcp_options_id"] == "dhcp-uuid-1"


def test_stateless_adds_the_flag_and_pure_slaac_creates_nothing(lab):
    _, client, calls = lab
    _subnet(client, "a", "v6only", "fd42:1::/64", ip_version=6, ipv6_ra_mode="dhcpv6-stateless", ipv6_address_mode="dhcpv6-stateless")
    _subnet(client, "b", "dual", "fd42:2::/64", ip_version=6, ipv6_ra_mode="slaac", ipv6_address_mode="slaac")
    created = _created_options(calls)
    assert len(created) == 1 and "options:dhcpv6_stateless=true" in created[0]


def test_dns_servers_reach_the_dhcpv6_row(lab):
    _, client, calls = lab
    _subnet(client, "s6", "v6only", "fd42:7:7::/64", ip_version=6, dns_nameservers=["2001:4860:4860::8888"])
    (args,) = _created_options(calls)
    assert "options:dns_server={2001:4860:4860::8888}" in args


def test_a_port_is_bound_to_the_dhcpv6_options_without_failing_when_ovn_refuses(lab, monkeypatch):
    networking, client, calls = lab
    _subnet(client, "s6", "v6only", "fd42:7:7::/64", ip_version=6)
    assert _port(client, "p1", "v6only").status_code == 201
    bind = [(a, k) for a, k in calls if a[:1] == ("lsp-set-dhcpv6-options",)]
    assert bind == [(("lsp-set-dhcpv6-options", "p1", "dhcp-uuid-1"), {"check": False})]
    assert [a for a, _ in calls if a[:1] == ("lsp-set-addresses",)][-1][2].endswith("fd42:7:7::1")  # no gateway to skip
