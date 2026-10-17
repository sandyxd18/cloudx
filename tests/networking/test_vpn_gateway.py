from unittest.mock import MagicMock
import pytest
from fastapi import HTTPException
from sqlalchemy import select

def test_vpn_gateway_api_comprehensive(networking, monkeypatch):
    net = networking
    from services.networking.app.routers import vpn

    monkeypatch.setattr(vpn, "_get_router_host", lambda rid: "cloudx-compute-01")
    monkeypatch.setattr(net.ovn_cli, "ovn", lambda *a, **k: "uuid-ok")

    db = net.db.SessionLocal()
    ext_net = net.models.Network(name="ext", external=True, provider=True, provider_cidr="172.24.4.0/24", gateway_ip="172.24.4.1", allocation_start="172.24.4.100", allocation_end="172.24.4.200")
    r_no_ext = net.models.Router(name="router-no-ext", project_id="p1")
    r = net.models.Router(name="router-vpn", project_id="p1")
    ike = net.models.IKEPolicy(name="ike", project_id="p1")
    ipsec = net.models.IPsecPolicy(name="ipsec", project_id="p1")
    db.add_all([ext_net, r_no_ext, r, ike, ipsec])
    db.commit()
    db.refresh(r_no_ext)
    db.refresh(r)
    db.refresh(ike)
    db.refresh(ipsec)

    payload = net.schemas.VPNSiteConnectionIn(
        name="conn1",
        router_id=r.id,
        peer_address="1.2.3.4",
        peer_id="peer",
        peer_cidrs=["192.168.1.0/24"],
        psk="secret-psk-123",
        ikepolicy_id=ike.id,
        ipsecpolicy_id=ipsec.id
    )

    # 1. Without external gateway
    payload_no_ext = net.schemas.VPNSiteConnectionIn(
        name="conn-no-ext",
        router_id=r_no_ext.id,
        peer_address="1.2.3.4",
        peer_id="peer",
        peer_cidrs=["192.168.1.0/24"],
        psk="secret-psk-123",
        ikepolicy_id=ike.id,
        ipsecpolicy_id=ipsec.id
    )
    with pytest.raises(HTTPException) as exc:
        vpn.create_vpn_site_connection(payload_no_ext, {"project_id": "p1", "role": "member"}, db)
    assert exc.value.status_code == 400

    r.external_network_id = ext_net.id
    db.commit()

    # 2. Create valid connection
    conn = vpn.create_vpn_site_connection(payload, {"project_id": "p1", "role": "member"}, db)
    assert conn["status"] == "PENDING_CREATE"
    assert conn["gateway_id"] is not None

    # 3. Overlapping peer CIDR -> 409
    payload_overlap = net.schemas.VPNSiteConnectionIn(
        name="conn2",
        router_id=r.id,
        peer_address="1.2.3.5",
        peer_id="peer2",
        peer_cidrs=["192.168.1.0/24"],
        psk="secret-psk-123",
        ikepolicy_id=ike.id,
        ipsecpolicy_id=ipsec.id
    )
    with pytest.raises(HTTPException) as exc:
        vpn.create_vpn_site_connection(payload_overlap, {"project_id": "p1", "role": "member"}, db)
    assert exc.value.status_code == 409

    # 4. Internal agent endpoints, over HTTP so the InternalAuth dependency really runs
    from fastapi.testclient import TestClient
    monkeypatch.setenv("INTERNAL_TOKEN", "t" * 32)
    c = TestClient(net.app)
    assert c.get("/internal/vpn-agent/cloudx-compute-01").status_code in (401, 403)
    h = {"X-Internal-Token": "t" * 32}
    r_ = c.get("/internal/vpn-agent/cloudx-compute-01", headers=h)
    assert r_.status_code == 200, r_.text
    agent_data = r_.json()
    assert len(agent_data["gateways"]) == 1
    assert len(agent_data["gateways"][0]["connections"]) == 1

    # the worker sends {"status": ..., "status_reason": ...}
    r_ = c.post("/internal/vpn-agent/cloudx-compute-01/status", headers=h,
                json={"statuses": {conn["id"]: {"status": "ACTIVE", "status_reason": "ok"}}})
    assert r_.status_code == 200, r_.text
    db.expire_all()
    updated_conn = vpn.get_vpn_site_connection(conn["id"], {"project_id": "p1", "role": "member"}, db)
    assert updated_conn["status"] == "ACTIVE"
    assert updated_conn["status_reason"] == "ok"

    # 5. Delete connection (last connection removes gateway and OVN objects)
    vpn.delete_vpn_site_connection(conn["id"], {"project_id": "p1", "role": "member"}, db)
    assert db.get(net.models.VPNSiteConnection, conn["id"]) is None
    assert db.scalar(select(net.models.VPNGateway).where(net.models.VPNGateway.router_id == r.id)) is None

def test_get_router_host_resolves_hostname(monkeypatch):
    from services.networking.app.routers import vpn
    # real lab output: "<lrp>-<chassis uuid>    <priority>"; SB Chassis.name is the uuid, hostname differs
    uuid = "d3c1649b-5374-4ac6-96aa-e8f157c82b75"
    monkeypatch.setattr(vpn.ovn_cli, "ovn", lambda *a, **k: f"lrp-router1-external-{uuid}    20")
    seen = []
    monkeypatch.setattr(vpn.ovn_cli, "sbctl", lambda *a: seen.append(a) or "cloudx-compute-01")
    assert vpn._get_router_host("router1") == "cloudx-compute-01"
    assert f"name={uuid}" in seen[0]
    monkeypatch.setattr(vpn.ovn_cli, "ovn", lambda *a, **k: "")
    with pytest.raises(HTTPException):
        vpn._get_router_host("router1")

def test_vpn_gateway_ovn_peer_lsp_and_distinct_macs(monkeypatch):
    from services.networking.app.routers import vpn
    ovn_calls = []
    monkeypatch.setattr(vpn.ovn_cli, "ovn", lambda *a, **k: ovn_calls.append(a))

    db = MagicMock()
    gw = MagicMock()
    gw.id = "vpngw-123456789abc"
    gw.transit_cidr = "100.101.0.0/30"
    gw.inner_mac = "fa:16:3e:01:02:03"
    gw.outer_mac = "fa:16:3e:04:05:06"
    gw.outer_ip = "172.24.4.100"

    router = MagicMock()
    router.name = "router-1"

    ext_net = MagicMock()
    ext_net.name = "public-net"

    short = vpn._gw_short(gw.id)
    vpn._apply_vpn_gateway_ovn(db, gw, router, ext_net)

    # Assert peer lsp exists and LRP MAC is distinct from inner_mac
    lrp_add_calls = [c for c in ovn_calls if "lrp-add" in c]
    assert lrp_add_calls
    lrp_mac_used = lrp_add_calls[0][3]
    assert lrp_mac_used != gw.inner_mac

    lsp_add_peer = [c for c in ovn_calls if "lsp-add" in c and f"lsp-vpn-{short}-lrp" in c]
    assert lsp_add_peer
    assert "type=router" in lsp_add_peer[0]
    assert f"options:router-port=lrp-vpn-{short}" in lsp_add_peer[0]

    # Rollback / delete cleanup path includes lsp-del for peer lsp
    rollback_calls = []
    monkeypatch.setattr(vpn.ovn_cli, "ovn", lambda *a, **k: rollback_calls.append(a))
    vpn._rollback_vpn_gateway_ovn(gw.id, router.name, ext_net.name)
    assert any("lsp-del" in c and f"lsp-vpn-{short}-lrp" in c for c in rollback_calls)

