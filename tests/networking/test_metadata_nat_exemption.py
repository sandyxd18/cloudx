"""Metadata requests (169.254.169.254) must reach the metadata proxy with the VM's own address. The router's NAT rules
(`snat` for a subnet, `dnat_and_snat` for a floating IP) rewrote the source to the router or floating address, so the proxy,
which looks a VM up by its fixed IP, answered 404. Every NAT row of a router now carries the address set `cloudx_metadata`
as `exempted_ext_ips`: traffic to that address is left alone."""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from shared.auth_middleware import require_auth

AS_UUID = "11111111-aaaa-bbbb-cccc-000000000001"
NAT_A = "22222222-aaaa-bbbb-cccc-00000000000a"
NAT_B = "33333333-aaaa-bbbb-cccc-00000000000b"


class FakeOvn:
    """Answers the few ovn-nbctl queries the exemption makes and records every call."""

    def __init__(self, address_set="", nat_rows=""):
        self.calls = []
        self.address_set = address_set
        self.nat_rows = nat_rows
        self.create_fails = False

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if "find" in args and "address_set" in args:
            return self.address_set
        if args[:1] == ("create",):
            if self.create_fails:
                self.address_set = AS_UUID  # another networking-api instance won the race
                return ""
            self.address_set = AS_UUID
            return AS_UUID
        if args[:3] == ("get", "logical_router", "r1"):
            return self.nat_rows
        return ""

    def sets(self):
        return [a for a, _ in self.calls if "set" in a and "nat" in a]


@pytest.fixture
def ovn(networking, monkeypatch):
    fake = FakeOvn(nat_rows=f"[{NAT_A}, {NAT_B}]")
    monkeypatch.setattr(networking.ovn_cli, "ovn", fake)
    return fake


def test_it_creates_the_address_set_once_and_exempts_every_nat_row_in_one_call(networking, ovn):
    networking.ovn_cli.exempt_metadata_from_nat("r1")

    creates = [a for a, _ in ovn.calls if a[:1] == ("create",)]
    assert creates == [("create", "address_set", "name=cloudx_metadata", "addresses=169.254.169.254")]
    (setting,) = ovn.sets()
    assert setting == ("set", "nat", NAT_A, f"exempted_ext_ips={AS_UUID}", "--", "set", "nat", NAT_B, f"exempted_ext_ips={AS_UUID}")


def test_it_reuses_an_existing_address_set(networking, ovn):
    ovn.address_set = AS_UUID

    networking.ovn_cli.exempt_metadata_from_nat("r1")

    assert [a for a, _ in ovn.calls if a[:1] == ("create",)] == []
    assert len(ovn.sets()) == 1


def test_it_finds_the_address_set_another_instance_created_at_the_same_moment(networking, ovn):
    ovn.create_fails = True

    networking.ovn_cli.exempt_metadata_from_nat("r1")

    (setting,) = ovn.sets()
    assert f"exempted_ext_ips={AS_UUID}" in setting


@pytest.mark.parametrize("rows", ["", "[]"])
def test_a_router_without_nat_rows_is_left_alone(networking, ovn, rows):
    ovn.nat_rows = rows

    networking.ovn_cli.exempt_metadata_from_nat("r1")

    assert ovn.sets() == []


def test_a_failing_ovn_never_fails_the_caller(networking, monkeypatch):
    def broken(*args, **kwargs):
        raise HTTPException(status_code=502, detail="ovn-nbctl failed")

    monkeypatch.setattr(networking.ovn_cli, "ovn", broken)

    networking.ovn_cli.exempt_metadata_from_nat("r1")  # no exception


def test_the_address_set_holds_the_metadata_address_only(networking):
    assert networking.ovn_cli.METADATA_ADDRESS_SET == "cloudx_metadata"
    assert networking.config.METADATA_LINK_LOCAL == "169.254.169.254"


@pytest.fixture
def cloud(networking, monkeypatch):
    networking.app.dependency_overrides[require_auth] = lambda: {"project_id": "proj-1", "role": "admin"}
    fake = FakeOvn(nat_rows=f"[{NAT_A}]")
    monkeypatch.setattr(networking.ovn_cli, "ovn", fake)
    with networking.db.SessionLocal() as db:
        db.add_all([
            networking.models.SecurityGroup(id="sg-default", name="default"),
            networking.models.Network(id="ext", name="public", external=True, provider=True, provider_cidr="172.22.0.0/24",
                                      gateway_ip="172.22.0.1", allocation_start="172.22.0.100", allocation_end="172.22.0.110"),
            networking.models.Network(id="n1", name="tenant"),
        ])
        db.commit()
    return TestClient(networking.app, raise_server_exceptions=False), fake


def _exempted(fake):
    return len(fake.sets())


def test_adding_a_router_interface_exempts_the_new_snat_row(networking, cloud):
    client, fake = cloud
    assert client.post("/routers", json={"name": "r1"}).status_code == 201
    sub = client.post("/subnets", json={"name": "s1", "network": "tenant", "cidr": "10.10.0.0/24", "gateway_ip": "10.10.0.1"})
    assert sub.status_code == 201, sub.text
    client.post("/routers/r1/gateway", json={"external_network": "public"})
    before = _exempted(fake)

    assert client.post("/routers/r1/interfaces", json={"subnet": sub.json()["id"]}).status_code == 201

    assert _exempted(fake) > before


def test_setting_the_gateway_exempts_the_snat_rows(networking, cloud):
    client, fake = cloud
    client.post("/routers", json={"name": "r1"})
    sub = client.post("/subnets", json={"name": "s1", "network": "tenant", "cidr": "10.10.0.0/24", "gateway_ip": "10.10.0.1"}).json()
    client.post("/routers/r1/interfaces", json={"subnet": sub["id"]})
    before = _exempted(fake)

    assert client.post("/routers/r1/gateway", json={"external_network": "public"}).status_code == 200

    assert _exempted(fake) > before


def test_startup_exempts_the_nat_rows_of_routers_that_already_exist(networking, cloud):
    client, fake = cloud
    client.post("/routers", json={"name": "r1"})
    client.post("/routers", json={"name": "r2"})
    fake.calls.clear()

    networking.bootstrap.bootstrap_metadata_nat_exemption()

    got = [a for a, _ in fake.calls if a[:2] == ("get", "logical_router")]
    assert sorted(a[2] for a in got) == ["r1", "r2"]


def _fip_cloud(networking, client):
    """A router with a gateway and a port with a fixed IP, ready for a floating IP."""
    assert client.post("/routers", json={"name": "r1"}).status_code == 201
    sub = client.post("/subnets", json={"name": "s1", "network": "tenant", "cidr": "10.10.0.0/24", "gateway_ip": "10.10.0.1"}).json()
    client.post("/routers/r1/interfaces", json={"subnet": sub["id"]})
    client.post("/routers/r1/gateway", json={"external_network": "public"})
    with networking.db.SessionLocal() as db:
        db.add(networking.models.Port(id="p1", name="vm-p1-port", network_id="n1", mac_address="fa:16:3e:00:00:05", fixed_ip="10.10.0.5", ip_address="10.10.0.5"))
        db.commit()


def test_associating_a_floating_ip_exempts_its_nat_row(networking, cloud):
    client, fake = cloud
    _fip_cloud(networking, client)
    fip = client.post("/floating-ips", json={"external_network": "public"})
    assert fip.status_code in (200, 201), fip.text
    before = _exempted(fake)

    res = client.post(f"/floating-ips/{fip.json()['id']}/associate", json={"port": "p1", "router": "r1"})

    assert res.status_code == 200, res.text
    assert any(a[:1] == ("--may-exist",) and "dnat_and_snat" in a for a, _ in fake.calls)
    assert _exempted(fake) > before


def test_creating_a_floating_ip_with_a_port_exempts_its_nat_row(networking, cloud):
    client, fake = cloud
    _fip_cloud(networking, client)
    before = _exempted(fake)

    res = client.post("/floating-ips", json={"external_network": "public", "fixed_port": "p1"})

    assert res.status_code in (200, 201), res.text
    assert _exempted(fake) > before
