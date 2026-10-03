import importlib
import os
import subprocess
import sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

def test_key_manager_secrets_crud(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "key_manager.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("KEY_MANAGER_MASTER_KEY", "super-secret-master-key-32bytes-len!!")

    import services.key_manager.app.main as km_main
    km_main = importlib.reload(km_main)
    km_main.Base.metadata.create_all(bind=km_main.engine)

    client = TestClient(km_main.app)

    # 1. Health check
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"
    assert res.json()["service"] == "key-manager-api"

    # 2. Create Secret (Symmetric Key)
    payload = {
        "name": "luks-disk-key-1",
        "secret_type": "symmetric",
        "payload": "super-secret-passphrase-12345",
        "algorithm": "aes",
        "bit_length": 256
    }
    res = client.post("/secrets", json=payload, headers={"X-Project-Id": "proj-admin"})
    assert res.status_code == 201
    secret_data = res.json()
    assert secret_data["name"] == "luks-disk-key-1"
    assert secret_data["secret_type"] == "symmetric"
    secret_id = secret_data["id"]

    # 3. List Secrets
    res = client.get("/secrets", headers={"X-Project-Id": "proj-admin"})
    assert res.status_code == 200
    secrets_list = res.json()
    assert len(secrets_list) == 1
    assert secrets_list[0]["id"] == secret_id

    # 4. Get Secret Detail (with decrypted payload)
    res = client.get(f"/secrets/{secret_id}", headers={"X-Project-Id": "proj-admin"})
    assert res.status_code == 200
    detail = res.json()
    assert detail["payload"] == "super-secret-passphrase-12345"

    # 5. Create Container (Grouping secrets)
    container_payload = {
        "name": "tls-cert-bundle",
        "container_type": "certificate",
        "secret_refs": [secret_id]
    }
    res = client.post("/containers", json=container_payload, headers={"X-Project-Id": "proj-admin"})
    assert res.status_code == 201
    container_data = res.json()
    assert container_data["name"] == "tls-cert-bundle"
    container_id = container_data["id"]

    # 6. List Containers
    res = client.get("/containers", headers={"X-Project-Id": "proj-admin"})
    assert res.status_code == 200
    containers = res.json()
    assert len(containers) == 1
    assert containers[0]["id"] == container_id

    # 7. Delete Secret & Container
    res = client.delete(f"/containers/{container_id}", headers={"X-Project-Id": "proj-admin"})
    assert res.status_code == 204

    res = client.delete(f"/secrets/{secret_id}", headers={"X-Project-Id": "proj-admin"})
    assert res.status_code == 204

    # Verify deletion
    res = client.get(f"/secrets/{secret_id}", headers={"X-Project-Id": "proj-admin"})
    assert res.status_code == 404

def test_key_manager_cli_help():
    cli_main = Path(__file__).resolve().parents[1] / "cli" / "cloudx_cli" / "main.py"
    res = subprocess.run([sys.executable, str(cli_main), "secret", "--help"], capture_output=True, text=True)
    assert res.returncode == 0
    assert "secret" in res.stdout

    res2 = subprocess.run([sys.executable, str(cli_main), "secret-container", "--help"], capture_output=True, text=True)
    assert res2.returncode == 0
    assert "secret-container" in res2.stdout

    res3 = subprocess.run([sys.executable, str(cli_main), "vpn", "--help"], capture_output=True, text=True)
    assert res3.returncode == 0
    assert "vpn" in res3.stdout

def test_networking_vpnaas_crud(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "networking_vpn.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

    import services.networking.app.main as net_main
    net_main = importlib.reload(net_main)
    net_main.Base.metadata.create_all(bind=net_main.engine)

    try:
        from shared.auth_middleware import require_auth
    except ImportError:
        from services.shared.auth_middleware import require_auth

    net_main.app.dependency_overrides[require_auth] = lambda: {"project_id": "proj-admin", "role": "admin"}
    monkeypatch.setattr(net_main, "ovn", lambda *args, **kwargs: "")

    client = TestClient(net_main.app)

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

    # 3. Create Router first
    router_res = client.post("/routers", json={"name": "vpn-router-1"}, headers={"X-Project-Id": "proj-admin", "X-Role": "admin"})
    assert router_res.status_code == 201
    router_id = router_res.json()["id"]

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
    assert "conn office-site-connection" in cfg["ipsec_conf"]
    assert "super-secret-psk-key-123" in cfg["ipsec_secrets"]

def test_block_store_volume_replication_dr(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "block_store_rep.sqlite"
    volume_root = tmp_path / "volumes"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("STORAGE_LVM_DRIVER", "true")

    import services.block_store.app.main as bs_main
    bs_main = importlib.reload(bs_main)
    bs_main.Base.metadata.create_all(bind=bs_main.engine)

    try:
        from shared.auth_middleware import require_auth
    except ImportError:
        from services.shared.auth_middleware import require_auth

    bs_main.app.dependency_overrides[require_auth] = lambda: {"project_id": "proj-1", "role": "admin"}

    client = TestClient(bs_main.app)

    db = bs_main.SessionLocal()
    volume_path = volume_root / "vol-dr.qcow2"
    volume_path.parent.mkdir(parents=True, exist_ok=True)
    volume_path.write_bytes(b"data-content-for-dr")
    vt = bs_main.VolumeType(id="vt-default", name="default", backend="lvm", is_public=True)
    volume = bs_main.Volume(id="vol-dr-1", name="vol-dr-1", size_gb=10, status="available", path=str(volume_path), bootable=False)
    db.add_all([vt, volume])
    db.commit()

    # 1. Enable Volume Replication
    rep_body = {"target": "dr-pool-node-2", "mode": "async"}
    res = client.post("/volumes/vol-dr-1/enable-replication", json=rep_body, headers={"X-Project-Id": "proj-1", "X-Role": "admin"})
    assert res.status_code == 200
    vol_rep = res.json()
    assert vol_rep["replication_status"] == "enabled"
    assert vol_rep["replication_target"] == "dr-pool-node-2"

    # 2. Sync Replication (Manual or background trigger)
    res = client.post("/volumes/vol-dr-1/sync-replication", headers={"X-Project-Id": "proj-1", "X-Role": "admin"})
    assert res.status_code == 200
    sync_res = res.json()
    assert sync_res["status"] == "synced"
    assert sync_res["last_replicated_at"] is not None

    # 3. Failover Volume (Disaster Recovery promotion)
    res = client.post("/volumes/vol-dr-1/failover", headers={"X-Project-Id": "proj-1", "X-Role": "admin"})
    assert res.status_code == 200
    failover_res = res.json()
    assert failover_res["replication_status"] == "failed-over"

    # 4. Disable Replication
    res = client.post("/volumes/vol-dr-1/disable-replication", headers={"X-Project-Id": "proj-1", "X-Role": "admin"})
    assert res.status_code == 200
    assert res.json()["replication_status"] == "disabled"
