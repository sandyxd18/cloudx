
from fastapi.testclient import TestClient


def test_networking_port_qos_policy(monkeypatch, networking):

    from shared.auth_middleware import require_auth
    networking.app.dependency_overrides[require_auth] = lambda: {"project_id": "proj-1", "role": "admin"}
    monkeypatch.setattr(networking.ovn_cli, "ovn", lambda *args, **kwargs: "")
    client = TestClient(networking.app)

    # Seed network & subnet
    with networking.db.SessionLocal() as db:
        net = networking.models.Network(id="net-1", name="private-net")
        sub = networking.models.Subnet(id="sub-1", name="sub-1", network_id="net-1", cidr="10.0.0.0/24", gateway="10.0.0.1", ip_version=4)
        sg = networking.models.SecurityGroup(id="sg-default", name="default")
        db.add_all([net, sub, sg])
        db.commit()

    # Create port with QoS
    res = client.post(
        "/ports",
        json={"name": "port-qos-1", "network": "private-net", "qos_max_kbps": 10000, "qos_burst_kbps": 20000},
    )
    assert res.status_code in (200, 201)
    data = res.json()
    assert data["qos_policy"] == {"max_kbps": 10000, "burst_kbps": 20000}

    # PATCH port QoS
    port_id = data["id"]
    res_patch = client.patch(f"/ports/{port_id}", json={"qos_max_kbps": 50000})
    assert res_patch.status_code == 200
    assert res_patch.json()["qos_policy"]["max_kbps"] == 50000
