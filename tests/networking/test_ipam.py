"""Fixed-IP allocation and allocation-range rules (pure logic on a sqlite database)."""
import pytest
from fastapi import HTTPException


@pytest.fixture
def make_subnet(networking):
    """Create a subnet (and its network) and hand back (db session, subnet)."""
    sessions = []

    def make(cidr, gateway=None, start=None, end=None, version=4, name="sub"):
        db = networking.db.SessionLocal()
        sessions.append(db)
        net = networking.models.Network(id=f"net-{name}", name=f"net-{name}")
        subnet = networking.models.Subnet(
            id=f"sub-{name}", name=name, network_id=net.id, cidr=cidr, gateway=gateway, gateway_ip=gateway,
            allocation_start=start, allocation_end=end, ip_version=version)
        db.add_all([net, subnet])
        db.commit()
        return db, subnet

    yield make
    for db in sessions:
        db.close()


def take(networking, db, subnet, ip):
    """Record `ip` as used by a port."""
    n = db.query(networking.models.Port).count()
    db.add(networking.models.Port(name=f"p{n}", network_id=subnet.network_id, subnet_id=subnet.id,
                                  mac_address=f"02:00:00:00:00:{n:02x}", fixed_ip=ip))
    db.commit()


def test_allocation_starts_after_the_gateway_and_skips_used_addresses(networking, make_subnet):
    db, subnet = make_subnet("10.1.0.0/29", gateway="10.1.0.1")  # hosts .1-.6, gateway .1
    first = networking.ipam.allocate_fixed_ip(db, subnet)
    assert first == "10.1.0.2"
    take(networking, db, subnet, first)
    assert networking.ipam.allocate_fixed_ip(db, subnet) == "10.1.0.3"


def test_a_released_address_is_handed_out_again(networking, make_subnet):
    db, subnet = make_subnet("10.1.0.0/29", gateway="10.1.0.1")
    for ip in ("10.1.0.2", "10.1.0.3", "10.1.0.4"):
        take(networking, db, subnet, ip)
    db.query(networking.models.Port).filter_by(fixed_ip="10.1.0.3").delete()
    db.commit()
    assert networking.ipam.allocate_fixed_ip(db, subnet) == "10.1.0.3"


def test_exhausted_subnet_is_a_conflict(networking, make_subnet):
    db, subnet = make_subnet("10.1.0.0/29", gateway="10.1.0.1")
    for ip in ("10.1.0.2", "10.1.0.3", "10.1.0.4", "10.1.0.5", "10.1.0.6"):
        take(networking, db, subnet, ip)
    with pytest.raises(HTTPException) as exc:
        networking.ipam.allocate_fixed_ip(db, subnet)
    assert exc.value.status_code == 409 and "no available IP" in exc.value.detail


@pytest.mark.parametrize("requested, status", [
    ("10.9.9.9", 400),      # outside the subnet
    ("10.1.0.1", 409),      # the gateway
    ("10.1.0.4", 409),      # already used
])
def test_requested_address_must_be_inside_free_and_not_the_gateway(networking, make_subnet, requested, status):
    db, subnet = make_subnet("10.1.0.0/29", gateway="10.1.0.1")
    take(networking, db, subnet, "10.1.0.4")
    with pytest.raises(HTTPException) as exc:
        networking.ipam.allocate_fixed_ip(db, subnet, requested=requested)
    assert exc.value.status_code == status


def test_a_free_requested_address_is_honoured(networking, make_subnet):
    db, subnet = make_subnet("10.1.0.0/29", gateway="10.1.0.1")
    assert networking.ipam.allocate_fixed_ip(db, subnet, requested="10.1.0.5") == "10.1.0.5"


def test_explicit_allocation_pool_is_respected(networking, make_subnet):
    db, subnet = make_subnet("10.1.0.0/24", gateway="10.1.0.1", start="10.1.0.100", end="10.1.0.101")
    take(networking, db, subnet, "10.1.0.100")
    assert networking.ipam.allocate_fixed_ip(db, subnet) == "10.1.0.101"
    take(networking, db, subnet, "10.1.0.101")
    with pytest.raises(HTTPException) as exc:
        networking.ipam.allocate_fixed_ip(db, subnet)
    assert exc.value.status_code == 409


def test_ipv6_allocation_starts_at_the_first_host(networking, make_subnet):
    db, subnet = make_subnet("fd00:1::/64", gateway="fd00:1::ff", version=6, name="v6")
    assert networking.ipam.allocate_fixed_ip(db, subnet) == "fd00:1::1"


@pytest.mark.parametrize("start, end", [("10.2.0.50", "10.2.0.40"), ("10.9.0.1", "10.2.0.40")])
def test_invalid_allocation_range_is_rejected(networking, make_subnet, start, end):
    db, subnet = make_subnet("10.2.0.0/24", gateway="10.2.0.1", start=start, end=end)
    with pytest.raises(HTTPException) as exc:
        networking.ipam.allocation_bounds(subnet)
    assert exc.value.status_code == 400


def test_default_pool_for_a_network_skips_the_gateway(networking):
    assert networking.ipam.allocation_bounds_for_network("192.168.56.0/24", "192.168.56.1", None, None) == ("192.168.56.2", "192.168.56.254")
    assert networking.ipam.allocation_bounds_for_network("192.168.56.0/24", None, None, None) == ("192.168.56.2", "192.168.56.254")
    with pytest.raises(HTTPException):
        networking.ipam.allocation_bounds_for_network("192.168.56.0/24", "192.168.56.1", "192.168.56.200", "192.168.56.10")


@pytest.mark.parametrize("cidr", ["10.0.0.0/24", "10.0.0.0/29", "10.0.0.0/30", "10.0.0.0/31", "10.0.0.1/32", "fd00::/120",
                                  "fd00::/126", "fd00::/127", "fd00::1/128"])
def test_usable_range_matches_ipaddress_hosts(networking, cidr):
    import ipaddress

    network = ipaddress.ip_network(cidr, strict=False)
    hosts = list(network.hosts())
    expected = (hosts[0], hosts[-1]) if hosts else None
    assert networking.ipam._host_range(network) == expected


def test_huge_subnets_do_not_hang(networking):
    """list(network.hosts()) on an IPv6 /64 never returns (and an IPv4 /8 is 16 million objects)."""
    import ipaddress
    import time

    started = time.monotonic()
    for cidr in ("fd00:1::/64", "10.0.0.0/8", "2001:db8::/32"):
        assert networking.ipam._host_range(ipaddress.ip_network(cidr)) is not None
    assert networking.ipam.allocation_bounds_for_network("10.0.0.0/8", "10.0.0.1", None, None) == ("10.0.0.2", "10.255.255.254")
    assert time.monotonic() - started < 2


def hold_ipv6(networking, db, network_id, ipv6, ipv4_subnet_id=None, n=None):
    """A dual-stack port: the IPv4 address and subnet_id belong to the IPv4 subnet, the IPv6 address is on the side."""
    n = db.query(networking.models.Port).count() if n is None else n
    db.add(networking.models.Port(name=f"d{n}", network_id=network_id, subnet_id=ipv4_subnet_id,
                                  mac_address=f"02:00:00:00:01:{n:02x}", ipv6_address=ipv6))
    db.commit()


def test_ipv6_held_by_a_dual_stack_port_is_not_handed_out_again(networking, make_subnet):
    # a second dual-stack port was given the first one's address and OVN refused it (HTTP 502)
    db, subnet = make_subnet("fd42:7:7::/64", gateway="fd42:7:7::1", version=6, name="v6")
    assert networking.ipam.allocate_fixed_ip(db, subnet) == "fd42:7:7::2"
    hold_ipv6(networking, db, subnet.network_id, "fd42:7:7::2")
    assert networking.ipam.allocate_fixed_ip(db, subnet) == "fd42:7:7::3"
    hold_ipv6(networking, db, subnet.network_id, "fd42:7:7::3")
    assert networking.ipam.allocate_fixed_ip(db, subnet) == "fd42:7:7::4"


def test_an_ipv6_address_is_only_taken_on_its_own_network_and_range(networking, make_subnet):
    db, subnet = make_subnet("fd42:7:7::/64", gateway="fd42:7:7::1", version=6, name="v6")
    hold_ipv6(networking, db, "some-other-network", "fd42:7:7::2")     # same range, another network
    hold_ipv6(networking, db, subnet.network_id, "fd42:9:9::2")        # same network, another subnet's range
    assert networking.ipam.allocate_fixed_ip(db, subnet) == "fd42:7:7::2"


def test_a_requested_ipv6_address_must_be_free_inside_the_subnet_and_not_the_gateway(networking, make_subnet):
    db, subnet = make_subnet("fd42:7:7::/64", gateway="fd42:7:7::1", version=6, name="v6")
    hold_ipv6(networking, db, subnet.network_id, "fd42:7:7::5")
    for requested, status in (("fd42:7:7::5", 409), ("fd42:7:7::1", 409), ("fd99::5", 400), ("10.0.0.5", 400), ("nope", 400)):
        with pytest.raises(HTTPException) as exc:
            networking.ipam.allocate_fixed_ip(db, subnet, requested=requested)
        assert exc.value.status_code == status, requested
    assert networking.ipam.allocate_fixed_ip(db, subnet, requested="fd42:7:7::6") == "fd42:7:7::6"


def test_a_garbage_requested_ipv4_address_is_a_400_not_a_crash(networking, make_subnet):
    db, subnet = make_subnet("10.1.0.0/29", gateway="10.1.0.1")
    with pytest.raises(HTTPException) as exc:
        networking.ipam.allocate_fixed_ip(db, subnet, requested="10.1.0")
    assert exc.value.status_code == 400


# ── EUI-64 addresses for subnets where the guest builds its own address (slaac, dhcpv6-stateless) ──

def test_eui64_address_follows_rfc_4291(networking):
    # RFC 4291 appendix A: the universal/local bit of the first octet is flipped, ff:fe goes in the middle
    assert networking.ipam.eui64_address("2001:db8:1:2::/64", "34:56:78:9a:bc:de") == "2001:db8:1:2:3656:78ff:fe9a:bcde"


def test_eui64_address_is_what_the_lab_guests_built(networking):
    # measured on the lab: CirrOS and Ubuntu 24.04 both built this address from the port's MAC on a slaac subnet
    assert networking.ipam.eui64_address("fd42:9:1::/64", "02:d3:2f:b8:2a:42") == "fd42:9:1:0:d3:2fff:feb8:2a42"


@pytest.mark.parametrize("version, mode, cidr, expected", [
    (6, "slaac", "fd42:9:1::/64", True),
    (6, "dhcpv6-stateless", "fd42:9:1::/64", True),
    (6, "dhcpv6-stateful", "fd42:9:1::/64", False),
    (6, None, "fd42:9:1::/64", False),
    (6, "slaac", "fd42:9:1::/80", False),      # EUI-64 needs a 64-bit interface id; an old subnet like this keeps counting up
    (4, "slaac", "10.1.0.0/24", False),
])
def test_only_slaac_and_stateless_64_subnets_use_eui64(networking, version, mode, cidr, expected):
    subnet = networking.models.Subnet(name="s", cidr=cidr, ip_version=version, ipv6_address_mode=mode)
    assert networking.ipam.uses_eui64(subnet) is expected


def _slaac_subnet(make_subnet, gateway="fd42:9:1::1"):
    db, subnet = make_subnet("fd42:9:1::/64", gateway=gateway, version=6, name="s6")
    subnet.ipv6_address_mode = "slaac"
    return db, subnet


def test_the_eui64_address_of_a_free_mac_is_allocated_and_the_mac_kept(networking, make_subnet):
    db, subnet = _slaac_subnet(make_subnet)
    got_mac, got_ip = networking.ipam.allocate_eui64_address(db, subnet, "02:00:00:00:00:01", lambda: pytest.fail("no new MAC needed"))
    assert (got_mac, got_ip) == ("02:00:00:00:00:01", "fd42:9:1::ff:fe00:1")


def test_a_taken_eui64_address_draws_a_new_mac(networking, make_subnet):
    db, subnet = _slaac_subnet(make_subnet)
    take(networking, db, subnet, networking.ipam.eui64_address(subnet.cidr, "02:00:00:00:00:01"))
    got_mac, got_ip = networking.ipam.allocate_eui64_address(db, subnet, "02:00:00:00:00:01", iter(["02:00:00:00:00:02"]).__next__)
    assert got_mac == "02:00:00:00:00:02" and got_ip == networking.ipam.eui64_address(subnet.cidr, "02:00:00:00:00:02")


def test_the_gateway_is_never_handed_out_as_an_eui64_address(networking, make_subnet):
    gateway = "fd42:9:1::ff:fe00:1"
    db, subnet = _slaac_subnet(make_subnet, gateway=gateway)
    got_mac, got_ip = networking.ipam.allocate_eui64_address(db, subnet, "02:00:00:00:00:01", iter(["02:00:00:00:00:02"]).__next__)
    assert got_mac == "02:00:00:00:00:02" and got_ip != gateway


def test_giving_up_on_a_taken_eui64_address_is_a_conflict(networking, make_subnet):
    db, subnet = _slaac_subnet(make_subnet)
    take(networking, db, subnet, networking.ipam.eui64_address(subnet.cidr, "02:00:00:00:00:01"))
    with pytest.raises(HTTPException) as exc:
        networking.ipam.allocate_eui64_address(db, subnet, "02:00:00:00:00:01", lambda: "02:00:00:00:00:01")
    assert exc.value.status_code == 409
