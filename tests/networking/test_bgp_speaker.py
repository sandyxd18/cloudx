from __future__ import annotations
import pytest
from fastapi import HTTPException
from sqlalchemy import select


def test_bgp_speaker_comprehensive(networking, monkeypatch):
    net = networking
    from services.networking.app.routers import routing, vpn

    ovn_calls = []
    def mock_ovn(*args, **kwargs):
        ovn_calls.append((args, kwargs))
        if kwargs.get("fail"):
            raise Exception("OVN command failed")
        return "uuid-ok"

    monkeypatch.setattr(net.ovn_cli, "ovn", mock_ovn)
    monkeypatch.setattr(vpn, "_get_router_host", lambda rid: "cloudx-compute-01" if "1" in rid else "cloudx-compute-02")

    db = net.db.SessionLocal()
    ext_net = net.models.Network(name="ext", external=True, provider=True, provider_cidr="172.24.4.0/24", gateway_ip="172.24.4.1", allocation_start="172.24.4.10", allocation_end="172.24.4.200")
    r1 = net.models.Router(name="router-1", project_id="p1")
    r2 = net.models.Router(name="router-2", project_id="p1")
    r_no_ext = net.models.Router(name="router-no-ext", project_id="p1")
    db.add_all([ext_net, r1, r2, r_no_ext])
    db.commit()
    db.refresh(r1)
    db.refresh(r2)
    db.refresh(r_no_ext)

    monkeypatch.setenv("INTERNAL_TOKEN", "t" * 32)
    from fastapi.testclient import TestClient
    c = TestClient(net.app)
    h_valid = {"X-Internal-Token": "t" * 32}
    h_user = {"X-Project-Id": "p1", "X-Role": "admin"}

    # 1. Internal Auth protection
    assert c.get("/internal/bgp-agent/cloudx-compute-01").status_code in (401, 403)
    assert c.post("/internal/bgp-agent/cloudx-compute-01/status", json={}).status_code in (401, 403)

    # 2. POST /bgp-peers without external gateway -> 400
    p_no_ext = net.schemas.BGPPeerIn(name="p-no-ext", router_id=r_no_ext.id, peer_ip="172.24.4.50", remote_as=65001, local_as=64512)
    with pytest.raises(HTTPException) as exc:
        routing.create_bgp_peer(p_no_ext, {"project_id": "p1", "role": "admin"}, db)
    assert exc.value.status_code == 400

    r1.external_network_id = ext_net.id
    r2.external_network_id = ext_net.id
    db.commit()

    # 3. Rollback on OVN failure during create
    # Force ovn failure for lsp-add
    orig_ovn = net.ovn_cli.ovn
    def fail_ovn(*args, **kwargs):
        if "lsp-add" in args:
            raise RuntimeError("OVN fail")
        return orig_ovn(*args, **kwargs)
    monkeypatch.setattr(net.ovn_cli, "ovn", fail_ovn)

    p_fail = net.schemas.BGPPeerIn(name="p-fail", router_id=r1.id, peer_ip="172.24.4.52", remote_as=65001, local_as=64512)
    with pytest.raises(HTTPException) as exc:
        routing.create_bgp_peer(p_fail, {"project_id": "p1", "role": "admin"}, db)
    assert exc.value.status_code == 500
    # Ensure speaker was cleaned up on rollback
    assert db.scalar(select(net.models.BGPSpeaker).where(net.models.BGPSpeaker.router_id == r1.id)) is None
    monkeypatch.setattr(net.ovn_cli, "ovn", orig_ovn)
    ovn_calls.clear()

    # 4. Valid create peer-1 on router-1
    p1_in = net.schemas.BGPPeerIn(name="peer-1", router_id=r1.id, peer_ip="172.24.4.50", remote_as=65001, local_as=64512, advertised_prefixes=["192.168.10.0/24"])
    peer1 = routing.create_bgp_peer(p1_in, {"project_id": "p1", "role": "admin"}, db)
    assert peer1["speaker_id"] is not None
    assert peer1["outer_ip"] is not None
    # Check exact OVN argv for lsp-add and lsp-set-addresses
    assert any("lsp-add" in str(c[0]) and f"lsp-bgp-" in str(c[0]) for c in ovn_calls)
    assert any("lsp-set-addresses" in str(c[0]) for c in ovn_calls)

    # 5. Local AS mismatch -> 409
    p_bad_as = net.schemas.BGPPeerIn(name="peer-bad", router_id=r1.id, peer_ip="172.24.4.51", remote_as=65001, local_as=64513)
    with pytest.raises(HTTPException) as exc:
        routing.create_bgp_peer(p_bad_as, {"project_id": "p1", "role": "admin"}, db)
    assert exc.value.status_code == 409

    # 6. Multi-host agent isolation: create peer-2 on router-2 (hosted on compute-02)
    p2_in = net.schemas.BGPPeerIn(name="peer-2", router_id=r2.id, peer_ip="172.24.4.60", remote_as=65002, local_as=64512, advertised_prefixes=["192.168.20.0/24"])
    peer2 = routing.create_bgp_peer(p2_in, {"project_id": "p1", "role": "admin"}, db)

    # Agent GET for compute-01 should only return router-1 speaker
    r_ag1 = c.get("/internal/bgp-agent/cloudx-compute-01", headers=h_valid)
    assert r_ag1.status_code == 200
    d1 = r_ag1.json()
    assert len(d1["speakers"]) == 1
    assert d1["speakers"][0]["router_id"] == r1.id

    r_ag2 = c.get("/internal/bgp-agent/cloudx-compute-02", headers=h_valid)
    assert r_ag2.status_code == 200
    d2 = r_ag2.json()
    assert len(d2["speakers"]) == 1
    assert d2["speakers"][0]["router_id"] == r2.id

    # 7. Agent Status Reconciliation (Established, route filtering, and safety checks)
    ovn_calls.clear()
    status_payload = {
        "peers": {
            peer1["id"]: {
                "status": "established",
                "status_reason": None,
                "routes": [
                    {"prefix": "0.0.0.0/0", "nexthop": "172.24.4.50"},       # default route -> ignored
                    {"prefix": "10.0.0.0/24", "nexthop": "172.24.4.50"},     # router subnet overlap -> ignored
                    {"prefix": "10.99.0.0/24", "nexthop": "1.1.1.1"},        # nexthop outside ext subnet -> ignored
                    {"prefix": "10.55.0.0/24", "nexthop": "172.24.4.50"},    # valid new route -> lr-route-add
                ]
            }
        }
    }
    r_st = c.post("/internal/bgp-agent/cloudx-compute-01/status", headers=h_valid, json=status_payload)
    assert r_st.status_code == 200

    # Verify lr-route-add called for 10.55.0.0/24 and NOT for invalid ones
    assert any("lr-route-add" in str(c[0]) and "10.55.0.0/24" in str(c[0]) for c in ovn_calls)
    assert not any("0.0.0.0/0" in str(c[0]) for c in ovn_calls)

    # 8. Nexthop update test: update route nexthop for 10.55.0.0/24
    ovn_calls.clear()
    status_payload_update = {
        "peers": {
            peer1["id"]: {
                "status": "established",
                "status_reason": None,
                "routes": [
                    {"prefix": "10.55.0.0/24", "nexthop": "172.24.4.55"},    # updated nexthop -> lr-route-del old, lr-route-add new
                ]
            }
        }
    }
    c.post("/internal/bgp-agent/cloudx-compute-01/status", headers=h_valid, json=status_payload_update)
    assert any("lr-route-del" in str(c[0]) for c in ovn_calls)
    assert any("lr-route-add" in str(c[0]) and "172.24.4.55" in str(c[0]) for c in ovn_calls)

    # 9. Down status pulls routes
    ovn_calls.clear()
    status_payload_down = {
        "peers": {
            peer1["id"]: {
                "status": "down",
                "status_reason": "connection lost",
                "routes": []
            }
        }
    }
    c.post("/internal/bgp-agent/cloudx-compute-01/status", headers=h_valid, json=status_payload_down)
    assert any("lr-route-del" in str(c[0]) for c in ovn_calls)

    # 10. Delete peer: deleting non-last peer (if we add another peer to router-1) vs deleting last peer
    # Add peer-1b to router-1
    p1b_in = net.schemas.BGPPeerIn(name="peer-1b", router_id=r1.id, peer_ip="172.24.4.52", remote_as=65001, local_as=64512)
    peer1b = routing.create_bgp_peer(p1b_in, {"project_id": "p1", "role": "admin"}, db)

    # Delete peer-1 -> speaker should remain because peer-1b exists
    routing.delete_bgp_peer(peer1["id"], {"project_id": "p1", "role": "admin"}, db)
    db.expire_all()
    speaker_check = db.scalar(select(net.models.BGPSpeaker).where(net.models.BGPSpeaker.router_id == r1.id))
    assert speaker_check is not None

    # Delete peer-1b -> last peer on router-1, speaker and LSP should be removed
    ovn_calls.clear()
    routing.delete_bgp_peer(peer1b["id"], {"project_id": "p1", "role": "admin"}, db)
    db.expire_all()
    assert db.scalar(select(net.models.BGPSpeaker).where(net.models.BGPSpeaker.router_id == r1.id)) is None
    assert any("lsp-del" in str(c[0]) for c in ovn_calls)

def test_bgp_reconcile_filters_and_pull(networking, monkeypatch):
    net = networking
    from services.networking.app.routers import routing, vpn
    from fastapi.testclient import TestClient

    calls = []
    monkeypatch.setattr(net.ovn_cli, "ovn", lambda *a, **k: calls.append(a) or "ok")
    monkeypatch.setattr(vpn, "_get_router_host", lambda name: "h1")
    db = net.db.SessionLocal()
    ext = net.models.Network(name="ext", external=True, provider=True, provider_cidr="172.24.4.0/24", gateway_ip="172.24.4.1",
                             allocation_start="172.24.4.10", allocation_end="172.24.4.200")
    sn = net.models.Subnet(name="s", network_id="n", cidr="10.0.0.0/24", gateway="10.0.0.1")
    db.add_all([ext, sn])
    db.commit()
    r = net.models.Router(name="rt", project_id="p1", external_network_id=ext.id,
                          routes='[{"destination": "10.77.0.0/24", "nexthop": "172.24.4.9"}]')
    db.add(r)
    db.commit()
    db.add(net.models.RouterInterface(router_id=r.id, subnet_id=sn.id, router_port_name="a", switch_port_name="b",
                                      mac_address="m", ip_address="10.0.0.1"))
    db.commit()
    cur = {"project_id": "p1", "role": "admin"}
    r0 = net.models.Router(name="noext", project_id="p1")
    db.add(r0)
    db.commit()
    calls.clear()
    with pytest.raises(HTTPException) as e:
        routing.create_bgp_peer(net.schemas.BGPPeerIn(name="z", router_id=r0.id, peer_ip="172.24.4.70", remote_as=65001, local_as=65000), cur, db)
    assert e.value.status_code == 400 and calls == []
    assert db.scalar(select(net.models.BGPSpeaker).where(net.models.BGPSpeaker.router_id == r0.id)) is None
    p1 = routing.create_bgp_peer(net.schemas.BGPPeerIn(name="a", router_id=r.id, peer_ip="172.24.4.50", remote_as=65001, local_as=65000), cur, db)
    p2 = routing.create_bgp_peer(net.schemas.BGPPeerIn(name="b", router_id=r.id, peer_ip="172.24.4.51", remote_as=65002, local_as=65000), cur, db)

    monkeypatch.setenv("INTERNAL_TOKEN", "t" * 32)
    c = TestClient(net.app)
    h = {"X-Internal-Token": "t" * 32}

    def post(pid, status, routes):
        calls.clear()
        assert c.post("/internal/bgp-agent/h1/status", headers=h,
                      json={"peers": {pid: {"status": status, "routes": routes}}}).status_code == 200
        return [x for x in calls if "lr-route-add" in x or "lr-route-del" in x]

    bad = [{"prefix": "0.0.0.0/0", "nexthop": "172.24.4.50"},
           {"prefix": "10.0.0.0/25", "nexthop": "172.24.4.50"},
           {"prefix": "10.0.0.0/8", "nexthop": "172.24.4.50"},
           {"prefix": "10.88.0.0/24", "nexthop": "1.1.1.1"},
           {"prefix": "10.77.0.0/24", "nexthop": "172.24.4.50"}]
    good = {"prefix": "10.55.0.0/24", "nexthop": "172.24.4.50"}
    assert post(p1["id"], "established", bad + [good]) == [("--may-exist", "lr-route-add", "rt", "10.55.0.0/24", "172.24.4.50")]
    # peer 2 must not touch peer 1's route
    assert post(p2["id"], "established", []) == []
    # down with routes still reported: everything pulled
    assert post(p1["id"], "down", [good]) == [("--if-exists", "lr-route-del", "rt", "10.55.0.0/24", "172.24.4.50")]
    # unknown host sees nothing, other host not returned
    assert c.get("/internal/bgp-agent/h2", headers=h).json() == {"speakers": []}
    assert len(c.get("/internal/bgp-agent/h1", headers=h).json()["speakers"]) == 1