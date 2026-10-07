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


def test_bgp_password_masked_and_scoped(networking):
    net = networking
    from services.networking.app.routers import routing

    db = net.db.SessionLocal()
    _seed(net, db)
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
