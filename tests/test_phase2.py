"""
Tests for Phase 2: Infrastructure Reliability & Networking Controls
- VM Auto-Recovery in compute-worker
- Port QoS / Rate limiting attributes in networking-api
"""

import importlib
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


def test_compute_worker_auto_recovery(monkeypatch):
    import services.compute_worker.app.main as worker_main

    # Mock conductor call to return a server in ACTIVE state whose domain is SHUTOFF
    fake_servers = [
        {
            "id": "srv-1",
            "name": "test-vm-crash",
            "status": "ACTIVE",
            "libvirt_domain_name": "dom-test-1",
        }
    ]

    restarted_domains = []

    monkeypatch.setattr(worker_main._rpc, "call", lambda target, method, args, timeout=10: {"servers": fake_servers})
    monkeypatch.setattr(worker_main.libvirt_driver, "get_all_domain_states", lambda: {"dom-test-1": "SHUTOFF"})
    # Mock libvirt_driver.start
    monkeypatch.setattr(worker_main.libvirt_driver, "start", lambda name: restarted_domains.append(name))
    monkeypatch.setattr(worker_main, "AUTO_RECOVERY", True)

    worker_main.sync_power_states()

    assert "dom-test-1" in restarted_domains


def test_networking_port_qos_policy(monkeypatch, tmp_path):
    db_path = tmp_path / "networking_qos.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    import services.networking.app.main as net_main

    net_main = importlib.reload(net_main)
    net_main.Base.metadata.create_all(bind=net_main.engine)

    from shared.auth_middleware import require_auth
    net_main.app.dependency_overrides[require_auth] = lambda: {"project_id": "proj-1", "role": "admin"}
    monkeypatch.setattr(net_main, "ovn", lambda *args, **kwargs: "")
    client = TestClient(net_main.app)

    # Seed network & subnet
    with net_main.SessionLocal() as db:
        net = net_main.Network(id="net-1", name="private-net")
        sub = net_main.Subnet(id="sub-1", name="sub-1", network_id="net-1", cidr="10.0.0.0/24", gateway="10.0.0.1", ip_version=4)
        sg = net_main.SecurityGroup(id="sg-default", name="default")
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
