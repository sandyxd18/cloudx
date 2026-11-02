"""A router port on an IPv6 subnet sends router advertisements. DHCPv6 gives an address but no prefix length and no routes,
so without them a DHCPv6 client got a /128 and could not reach its gateway or a neighbour."""
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
            networking.models.Network(id="n6", name="v6net"),
            networking.models.Network(id="n6m", name="v6small", mtu=1400),
            networking.models.Network(id="n4", name="v4net"),
        ])
        db.commit()
    client = TestClient(networking.app, raise_server_exceptions=False)
    client.post("/routers", json={"name": "r"})
    return networking, client, calls


def _ra(calls):
    """The ovn-nbctl `set logical_router_port <lrp> ipv6_ra_configs:...` calls, as (lrp, {key: value})."""
    found = []
    for args, kwargs in calls:
        if args[:2] == ("set", "logical_router_port") and any(a.startswith("ipv6_ra_configs:") for a in args):
            found.append((args[2], {a.split(":", 1)[1].split("=", 1)[0]: a.split("=", 1)[1] for a in args[3:]}, kwargs))
    return found


def _subnet(client, name, network, cidr, gateway, **modes):
    res = client.post("/subnets", json={"name": name, "network": network, "cidr": cidr, "gateway_ip": gateway,
                                        "ip_version": 6 if ":" in cidr else 4, **modes})
    assert res.status_code == 201, res.text
    return res.json()["id"]


@pytest.mark.parametrize("modes, address_mode", [
    ({"ipv6_ra_mode": "dhcpv6-stateful"}, "dhcpv6_stateful"),
    ({"ipv6_ra_mode": "dhcpv6-stateless"}, "dhcpv6_stateless"),
    ({"ipv6_ra_mode": "slaac"}, "slaac"),
    ({}, "dhcpv6_stateful"),                                               # no mode given: same default as the DHCPv6 row
    ({"ipv6_address_mode": "dhcpv6-stateless"}, "dhcpv6_stateless"),       # only the address mode given: follow it
])
def test_the_router_port_sends_advertisements_in_the_subnets_mode(lab, modes, address_mode):
    _, client, calls = lab
    sub = _subnet(client, "s6", "v6net", "fd42:7:7::/64", "fd42:7:7::1", **modes)
    assert client.post("/routers/r/interfaces", json={"subnet": sub}).status_code == 201
    (lrp, config, _), = _ra(calls)
    assert lrp == "lrp-r-s6"
    assert config["address_mode"] == f'"{address_mode}"'
    assert config["send_periodic"] == '"true"'


def test_the_advertisement_carries_the_networks_mtu(lab):
    _, client, calls = lab
    sub = _subnet(client, "s6m", "v6small", "fd42:7:9::/64", "fd42:7:9::1")
    client.post("/routers/r/interfaces", json={"subnet": sub})
    (_, config, _), = _ra(calls)
    assert config["mtu"] == '"1400"'


def test_an_ipv4_router_port_sends_none(lab):
    _, client, calls = lab
    sub = _subnet(client, "s4", "v4net", "10.20.0.0/24", "10.20.0.1")
    assert client.post("/routers/r/interfaces", json={"subnet": sub}).status_code == 201
    assert _ra(calls) == []


def test_a_refused_advertisement_setting_does_not_fail_the_interface(lab):
    # the static network-config still gives the guest its address and route: the interface is not worth a 502
    networking, client, calls = lab
    real = networking.ovn_cli.ovn

    def refusing(*args, **kwargs):
        if any(a.startswith("ipv6_ra_configs:") for a in args) and kwargs.get("check", True):
            raise RuntimeError("ovn-nbctl refused it")
        return real(*args, **kwargs)

    networking.ovn_cli.ovn = refusing
    sub = _subnet(client, "s6", "v6net", "fd42:7:7::/64", "fd42:7:7::1")
    assert client.post("/routers/r/interfaces", json={"subnet": sub}).status_code == 201
    assert _ra(calls)[0][2] == {"check": False}


def test_router_ports_that_exist_get_advertisements_when_networking_api_starts(lab):
    networking, client, calls = lab
    s6 = _subnet(client, "old6", "v6net", "fd42:7:7::/64", "fd42:7:7::1", ipv6_ra_mode="slaac")
    s4 = _subnet(client, "old4", "v4net", "10.20.0.0/24", "10.20.0.1")
    client.post("/routers/r/interfaces", json={"subnet": s6})
    client.post("/routers/r/interfaces", json={"subnet": s4})
    calls.clear()                                     # what was set when the ports were made does not count
    networking.bootstrap.bootstrap_router_advertisements()
    (lrp, config, _), = _ra(calls)
    assert lrp == "lrp-r-old6" and config["address_mode"] == '"slaac"'
    calls.clear()
    networking.bootstrap.bootstrap_router_advertisements()      # a restart does it again; setting a value twice is harmless
    assert len(_ra(calls)) == 1


# ── RDNSS (RFC 8106): the advertisement names a DNS server, which is all a slaac guest without cloud-init can learn it from ──

def test_the_advertisement_names_the_subnets_ipv6_dns_server(lab):
    _, client, calls = lab
    sub = _subnet(client, "dns6", "v6net", "fd42:7:a::/64", "fd42:7:a::1", ipv6_ra_mode="slaac", dns_nameservers=["2001:4860:4860::8888"])
    assert client.post("/routers/r/interfaces", json={"subnet": sub}).status_code == 201
    (_, config, _), = _ra(calls)
    assert config["rdnss"] == '"2001:4860:4860::8888"'


def test_only_the_first_ipv6_server_is_advertised_since_ovn_takes_one(lab):
    # ovn-nb(5): "OVN supports only one RDNSS server at this time"; an IPv4 address is no RDNSS at all
    _, client, calls = lab
    sub = _subnet(client, "dns6x", "v6net", "fd42:7:b::/64", "fd42:7:b::1",
                  dns_nameservers=["10.0.0.53", "2001:db8::53", "2001:db8::54"])
    client.post("/routers/r/interfaces", json={"subnet": sub})
    (_, config, _), = _ra(calls)
    assert config["rdnss"] == '"2001:db8::53"'


@pytest.mark.parametrize("nameservers", [[], ["10.0.0.53"]])
def test_without_an_ipv6_dns_server_the_advertisement_has_no_rdnss(lab, nameservers):
    _, client, calls = lab
    sub = _subnet(client, "nodns6", "v6net", "fd42:7:c::/64", "fd42:7:c::1", dns_nameservers=nameservers)
    client.post("/routers/r/interfaces", json={"subnet": sub})
    (_, config, _), = _ra(calls)
    assert "rdnss" not in config


def test_router_ports_that_exist_get_the_rdnss_when_networking_api_starts(lab):
    networking, client, calls = lab
    sub = _subnet(client, "olddns6", "v6net", "fd42:7:d::/64", "fd42:7:d::1", dns_nameservers=["2001:db8::53"])
    client.post("/routers/r/interfaces", json={"subnet": sub})
    calls.clear()
    networking.bootstrap.bootstrap_router_advertisements()
    (_, config, _), = _ra(calls)
    assert config["rdnss"] == '"2001:db8::53"'
