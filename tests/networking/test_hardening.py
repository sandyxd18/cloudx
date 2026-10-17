import pytest
from fastapi import HTTPException
from pydantic import ValidationError

A = {"project_id": "p1", "role": "member"}
B = {"project_id": "p2", "role": "member"}


def _seed(net, db):
    r1, r2 = net.models.Router(name="r1"), net.models.Router(name="r2")
    db.add_all([r1, r2])
    db.commit()
    return r1, r2


def test_peering_scoping_self_dup_and_ovn_failure(networking, monkeypatch):
    net = networking
    from services.networking.app.routers import routing

    monkeypatch.setattr(net.ovn_cli, "ovn", lambda *a, **k: "")
    db = net.db.SessionLocal()
    r1, r2 = _seed(net, db)
    with pytest.raises(HTTPException) as e:
        routing.create_vpc_peering("r1", net.schemas.VPCPeeringIn(name="x", accepter_router_id="r1"), A, db)
    assert e.value.status_code == 400
    p = routing.create_vpc_peering("r1", net.schemas.VPCPeeringIn(name="x", accepter_router_id="r2"), A, db)
    assert p.status == "active"
    with pytest.raises(HTTPException) as e:
        routing.create_vpc_peering("r2", net.schemas.VPCPeeringIn(name="y", accepter_router_id="r1"), A, db)
    assert e.value.status_code == 409
    for call in (lambda: routing.get_vpc_peering(p.id, B, db), lambda: routing.delete_vpc_peering(p.id, B, db)):
        with pytest.raises(HTTPException) as e:
            call()
        assert e.value.status_code == 404
    assert routing.list_vpc_peerings(B, db) == []

    def boom(*a, **k):
        raise HTTPException(status_code=502, detail="ovn down")

    monkeypatch.setattr(net.ovn_cli, "ovn", boom)
    db.add(net.models.Router(name="r3"))
    db.commit()
    with pytest.raises(HTTPException):
        routing.create_vpc_peering("r1", net.schemas.VPCPeeringIn(name="z", accepter_router_id="r3"), A, db)
    assert len(routing.list_vpc_peerings(A, db)) == 1


def test_bgp_password_masked_and_scoped(networking, monkeypatch):
    net = networking
    from services.networking.app.routers import routing, vpn

    monkeypatch.setattr(vpn, "_get_router_host", lambda name: "host-1")
    monkeypatch.setattr(net.ovn_cli, "ovn", lambda *a, **k: "")
    db = net.db.SessionLocal()
    r1, _ = _seed(net, db)
    ext = net.models.Network(name="ext", external=True, provider=True, provider_cidr="172.24.4.0/24", gateway_ip="172.24.4.1",
                             allocation_start="172.24.4.100", allocation_end="172.24.4.200")
    db.add(ext)
    db.commit()
    r1.external_network_id = ext.id
    db.commit()
    out = routing.create_bgp_peer(
        net.schemas.BGPPeerIn(name="b", router_id="r1", peer_ip="10.0.0.1", remote_as=65001, local_as=65000, auth_password="s3cret"),
        A, db)
    assert "auth_password" not in out and out["has_password"] is True
    with pytest.raises(HTTPException):
        routing.get_bgp_peer(out["id"], B, db)
    assert "s3cret" not in str(routing.get_bgp_peer(out["id"], A, db))


@pytest.mark.parametrize("field,val", [
    ("peer_ip", "10.0.0.1\n neighbor 1.1.1.1"), ("remote_as", 0), ("auth_password", "a b\nc"),
])
def test_bgp_input_injection_rejected(networking, field, val):
    base = dict(name="b", router_id="r1", peer_ip="10.0.0.1", remote_as=65001, local_as=65000)
    with pytest.raises(ValidationError):
        networking.schemas.BGPPeerIn(**{**base, field: val})


@pytest.mark.parametrize("field,val", [
    ("peer_address", "1.2.3.4\nright=x"), ("peer_id", "a b"), ("name", "n\nconn evil"),
    ("psk", "abcdefgh\nauto=add"), ("psk", 'abcdefg"h'), ("psk", "short"),
])
def test_vpn_input_injection_rejected(networking, field, val):
    base = dict(name="c", router_id="r", peer_address="vpn.example.com", peer_id="peer@x", peer_cidrs=["10.1.0.0/24"],
                psk="goodpsk-123", ikepolicy_id="i", ipsecpolicy_id="s")
    networking.schemas.VPNSiteConnectionIn(**base)
    with pytest.raises(ValidationError):
        networking.schemas.VPNSiteConnectionIn(**{**base, field: val})


def test_vpn_connection_scoped(networking):
    net = networking
    from services.networking.app.routers import vpn

    db = net.db.SessionLocal()
    c = net.models.VPNSiteConnection(name="c", project_id="p1", router_id="r", peer_address="1.2.3.4", peer_id="x",
                                     peer_cidrs_json="[]", psk="goodpsk-123", ikepolicy_id="i", ipsecpolicy_id="s",
                                     admin_state_up=True, status="ACTIVE")
    db.add(c)
    db.commit()
    for call in (lambda: vpn.get_vpn_site_connection(c.id, B, db), lambda: vpn.delete_vpn_site_connection(c.id, B, db),
                 lambda: vpn.generate_vpn_config(c.id, B, db)):
        with pytest.raises(HTTPException) as e:
            call()
        assert e.value.status_code == 404
    assert vpn.get_vpn_site_connection(c.id, A, db)["id"] == c.id


def test_vpc_peering_comprehensive(networking, monkeypatch):
    net = networking
    from services.networking.app.routers import routing, l3

    ovn_calls = []
    monkeypatch.setattr(net.ovn_cli, "ovn", lambda *a, **k: ovn_calls.append((a, k)))
    db = net.db.SessionLocal()
    r1 = net.models.Router(name="router1", project_id="p1")
    r2 = net.models.Router(name="router2", project_id="p1")
    r3 = net.models.Router(name="router3", project_id="p1")
    sub1 = net.models.Subnet(name="sub1", cidr="10.1.0.0/24", network_id="n1", gateway="10.1.0.1", project_id="p1")
    sub2 = net.models.Subnet(name="sub2", cidr="10.1.0.0/24", network_id="n2", gateway="10.1.0.1", project_id="p1") # overlaps sub1
    sub3 = net.models.Subnet(name="sub3", cidr="10.2.0.0/24", network_id="n3", gateway="10.2.0.1", project_id="p1")
    db.add_all([r1, r2, r3, sub1, sub2, sub3])
    db.commit()

    # Add interface with sub1 to r1
    iface1 = net.models.RouterInterface(router_id=r1.id, subnet_id=sub1.id, router_port_name="lrp-r1-sub1", switch_port_name="lsp-r1-sub1", mac_address="02:00:00:00:00:01", ip_address="10.1.0.1")
    db.add(iface1)
    db.commit()

    # Overlapping subnet check on create peering
    iface2_overlap = net.models.RouterInterface(router_id=r2.id, subnet_id=sub2.id, router_port_name="lrp-r2-sub2", switch_port_name="lsp-r2-sub2", mac_address="02:00:00:00:00:02", ip_address="10.1.0.1")
    db.add(iface2_overlap)
    db.commit()
    with pytest.raises(HTTPException) as exc:
        routing.create_vpc_peering("router1", net.schemas.VPCPeeringIn(name="peer-overlap", accepter_router_id=r2.id), A, db)
    assert exc.value.status_code == 409
    db.delete(iface2_overlap)
    db.commit()

    # Now add sub3 interface to r2 (non-overlapping)
    iface3 = net.models.RouterInterface(router_id=r2.id, subnet_id=sub3.id, router_port_name="lrp-r2-sub3", switch_port_name="lsp-r2-sub3", mac_address="02:00:00:00:00:03", ip_address="10.2.0.1")
    db.add(iface3)
    db.commit()

    # Create peering p1 -> check lrp-add has peer=
    ovn_calls.clear()
    p1 = routing.create_vpc_peering("router1", net.schemas.VPCPeeringIn(name="peering-1", accepter_router_id=r2.id), A, db)
    assert p1.transit_cidr is not None
    # Verify lrp-add contains peer=
    lrp_add_calls = [c for c in ovn_calls if "lrp-add" in c[0]]
    assert len(lrp_add_calls) >= 2
    assert any("peer=" in str(c) for c in lrp_add_calls)

    # Verify initial route installation on creation via sync_peering_routes
    import ipaddress
    hosts = list(ipaddress.ip_network(p1.transit_cidr).hosts())
    req_ip, acc_ip = str(hosts[0]), str(hosts[1])
    route_add_calls = [c[0] for c in ovn_calls if "lr-route-add" in c[0]]
    assert ("--may-exist", "lr-route-add", "router2", "10.1.0.0/24", req_ip) in route_add_calls
    assert ("--may-exist", "lr-route-add", "router1", "10.2.0.0/24", acc_ip) in route_add_calls

    # Second peering from router1 gets different transit_cidr
    p2 = routing.create_vpc_peering("router1", net.schemas.VPCPeeringIn(name="peering-2", accepter_router_id=r3.id), A, db)
    assert p2.transit_cidr != p1.transit_cidr

    # Adding a router interface triggers route on other router
    ovn_calls.clear()
    sub4 = net.models.Subnet(name="sub4", cidr="10.3.0.0/24", network_id="n4", gateway="10.3.0.1", project_id="p1")
    net_obj = net.models.Network(id="n4", name="n4", project_id="p1")
    db.add_all([net_obj, sub4])
    db.commit()
    l3.add_router_interface("router1", net.schemas.RouterInterfaceIn(subnet=sub4.id), A, db)
    assert any("lr-route-add" in str(c) for c in ovn_calls)

    # Removing router interface triggers lr-route-del
    ovn_calls.clear()
    l3.remove_router_interface("router1", sub4.id, A, db)
    assert any("lr-route-del" in str(c) for c in ovn_calls)

    # Delete peering removes ports and routes
    ovn_calls.clear()
    routing.delete_vpc_peering(p1.id, A, db)
    assert any("lrp-del" in str(c) for c in ovn_calls)
    assert any("lr-route-del" in str(c) for c in ovn_calls)
