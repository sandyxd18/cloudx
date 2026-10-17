from fastapi.testclient import TestClient


def test_networking_vpnaas_crud(monkeypatch, networking):

    try:
        from shared.auth_middleware import require_auth
    except ImportError:
        from shared.auth_middleware import require_auth

    networking.app.dependency_overrides[require_auth] = lambda: {"project_id": "proj-admin", "role": "admin"}
    monkeypatch.setattr(networking.ovn_cli, "ovn", lambda *args, **kwargs: "")

    client = TestClient(networking.app)

    # 1. Create IKE Policy
    ike_body = {
        "name": "ike-policy-1",
        "auth_algorithm": "sha256",
        "encryption_algorithm": "aes-256",
        "phase1_negotiation_mode": "main",
        "pfs": "group14"
    }
    res = client.post("/vpn/ikepolicies", json=ike_body, headers={"X-Project-Id": "proj-admin", "X-Role": "admin"})
    assert res.status_code == 201
    ike_data = res.json()
    assert ike_data["name"] == "ike-policy-1"
    ike_id = ike_data["id"]

    # 2. Create IPsec Policy
    ipsec_body = {
        "name": "ipsec-policy-1",
        "auth_algorithm": "sha256",
        "encryption_algorithm": "aes-256",
        "pfs": "group14",
        "transform_protocol": "esp"
    }
    res = client.post("/vpn/ipsecpolicies", json=ipsec_body, headers={"X-Project-Id": "proj-admin", "X-Role": "admin"})
    assert res.status_code == 201
    ipsec_data = res.json()
    assert ipsec_data["name"] == "ipsec-policy-1"
    ipsec_id = ipsec_data["id"]

    from services.networking.app.routers import vpn
    monkeypatch.setattr(vpn, "_get_router_host", lambda rid: "cloudx-compute-01")

    db = networking.db.SessionLocal()
    ext_net = networking.models.Network(name="ext", external=True, provider=True, provider_cidr="172.24.4.0/24", gateway_ip="172.24.4.1", allocation_start="172.24.4.100", allocation_end="172.24.4.200")
    db.add(ext_net)
    db.commit()
    db.refresh(ext_net)

    # 3. Create Router first
    router_res = client.post("/routers", json={"name": "vpn-router-1"}, headers={"X-Project-Id": "proj-admin", "X-Role": "admin"})
    assert router_res.status_code == 201
    router_id = router_res.json()["id"]

    router_obj = db.get(networking.models.Router, router_id)
    router_obj.external_network_id = ext_net.id
    db.commit()

    # 4. Create VPN Site Connection
    conn_body = {
        "name": "office-site-connection",
        "router_id": router_id,
        "peer_address": "203.0.113.10",
        "peer_id": "203.0.113.10",
        "peer_cidrs": ["192.168.1.0/24"],
        "psk": "super-secret-psk-key-123",
        "ikepolicy_id": ike_id,
        "ipsecpolicy_id": ipsec_id
    }
    res = client.post("/vpn/site-connections", json=conn_body, headers={"X-Project-Id": "proj-admin", "X-Role": "admin"})
    assert res.status_code == 201
    conn_data = res.json()
    assert conn_data["name"] == "office-site-connection"
    conn_id = conn_data["id"]

    # 5. List & Get VPN Site Connection
    res = client.get("/vpn/site-connections", headers={"X-Project-Id": "proj-admin", "X-Role": "admin"})
    assert res.status_code == 200
    assert len(res.json()) == 1

    # 6. Generate StrongSwan Config
    res = client.post(f"/vpn/site-connections/{conn_id}/config", headers={"X-Project-Id": "proj-admin", "X-Role": "admin"})
    assert res.status_code == 200
    cfg = res.json()
    assert "office-site-connection" in cfg["ipsec_conf"]
    assert "super-secret-psk-key-123" in cfg["ipsec_secrets"]
