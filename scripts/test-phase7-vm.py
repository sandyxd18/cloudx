#!/usr/bin/env python3
"""
Test Phase 7 features against live CloudX services (VM / Docker Compose stack).
"""

import os
import sys
import json
import time
import requests

KEY_MANAGER_URL = "http://127.0.0.1:8014"
NETWORKING_URL  = "http://127.0.0.1:8001"
BLOCK_STORE_URL = "http://127.0.0.1:8004"
AUTH_URL        = "http://127.0.0.1:8005"

def get_auth_token():
    auth_resp = requests.post(f"{AUTH_URL}/auth/tokens", json={"username": "admin", "password": os.environ["AUTH_ADMIN_PASSWORD"]})
    if auth_resp.status_code == 201:
        token = auth_resp.json().get("token")
        project_id = auth_resp.json().get("project", {}).get("id", "admin")
        return token, project_id
    raise RuntimeError(f"Failed to obtain auth token: {auth_resp.text}")

token, project_id = get_auth_token()

HEADERS_ADMIN = {
    "X-Auth-Token": token,
    "X-Project-Id": project_id,
    "X-Role": "admin"
}

def log(msg, status="INFO"):
    symbol = "✓" if status == "OK" else "❌" if status == "FAIL" else "ℹ"
    print(f"[{symbol}] {msg}")

def test_key_manager():
    log("=== Testing Key Manager API (Port 8014) ===")
    
    # Health check
    res = requests.get(f"{KEY_MANAGER_URL}/health")
    assert res.status_code == 200, f"Health check failed: {res.text}"
    log(f"Health check ok: {res.json()}", "OK")

    # Create secret
    sec_payload = {
        "name": "vm-luks-key-live",
        "secret_type": "symmetric",
        "payload": "live-vm-secret-passphrase-999",
        "algorithm": "aes",
        "bit_length": 256
    }
    res = requests.post(f"{KEY_MANAGER_URL}/secrets", json=sec_payload, headers=HEADERS_ADMIN)
    assert res.status_code == 201, f"Secret creation failed: {res.text}"
    sec_data = res.json()
    sec_id = sec_data["id"]
    log(f"Secret created: ID={sec_id}, Name={sec_data['name']}", "OK")

    # List secrets
    res = requests.get(f"{KEY_MANAGER_URL}/secrets", headers=HEADERS_ADMIN)
    assert res.status_code == 200
    secrets = res.json()
    assert any(s["id"] == sec_id for s in secrets)
    log(f"List secrets ok: found {len(secrets)} secret(s)", "OK")

    # Get secret detail (decrypted)
    res = requests.get(f"{KEY_MANAGER_URL}/secrets/{sec_id}", headers=HEADERS_ADMIN)
    assert res.status_code == 200
    detail = res.json()
    assert detail["payload"] == "live-vm-secret-passphrase-999"
    log(f"Decrypted payload matched: '{detail['payload']}'", "OK")

    # Create container
    ctr_payload = {
        "name": "vm-cert-bundle-live",
        "container_type": "certificate",
        "secret_refs": [sec_id]
    }
    res = requests.post(f"{KEY_MANAGER_URL}/containers", json=ctr_payload, headers=HEADERS_ADMIN)
    assert res.status_code == 201
    ctr_data = res.json()
    ctr_id = ctr_data["id"]
    log(f"Secret Container created: ID={ctr_id}, Name={ctr_data['name']}", "OK")

    # Delete container & secret
    res = requests.delete(f"{KEY_MANAGER_URL}/containers/{ctr_id}", headers=HEADERS_ADMIN)
    assert res.status_code == 204
    log(f"Deleted container {ctr_id}", "OK")

    res = requests.delete(f"{KEY_MANAGER_URL}/secrets/{sec_id}", headers=HEADERS_ADMIN)
    assert res.status_code == 204
    log(f"Deleted secret {sec_id}", "OK")

def test_networking_vpnaas():
    log("=== Testing VPNaaS (Networking API Port 8001) ===")

    # 1. Create IKE Policy
    ike_body = {
        "name": "live-ike-policy",
        "auth_algorithm": "sha256",
        "encryption_algorithm": "aes-256",
        "phase1_negotiation_mode": "main",
        "pfs": "group14"
    }
    res = requests.post(f"{NETWORKING_URL}/vpn/ikepolicies", json=ike_body, headers=HEADERS_ADMIN)
    assert res.status_code == 201, f"IKE policy creation failed: {res.text}"
    ike_id = res.json()["id"]
    log(f"IKE Policy created: ID={ike_id}", "OK")

    # 2. Create IPsec Policy
    ipsec_body = {
        "name": "live-ipsec-policy",
        "auth_algorithm": "sha256",
        "encryption_algorithm": "aes-256",
        "pfs": "group14",
        "transform_protocol": "esp"
    }
    res = requests.post(f"{NETWORKING_URL}/vpn/ipsecpolicies", json=ipsec_body, headers=HEADERS_ADMIN)
    assert res.status_code == 201, f"IPsec policy creation failed: {res.text}"
    ipsec_id = res.json()["id"]
    log(f"IPsec Policy created: ID={ipsec_id}", "OK")

    # 3. Create Router
    router_body = {"name": "live-vpn-router"}
    res = requests.post(f"{NETWORKING_URL}/routers", json=router_body, headers=HEADERS_ADMIN)
    assert res.status_code == 201, f"Router creation failed: {res.text}"
    router_id = res.json()["id"]
    log(f"Router created: ID={router_id}", "OK")

    # 4. Create VPN Site Connection
    conn_body = {
        "name": "live-site-conn-1",
        "router_id": router_id,
        "peer_address": "198.51.100.20",
        "peer_id": "198.51.100.20",
        "peer_cidrs": ["10.200.0.0/16"],
        "psk": "live-vm-vpn-psk-secret-777",
        "ikepolicy_id": ike_id,
        "ipsecpolicy_id": ipsec_id
    }
    res = requests.post(f"{NETWORKING_URL}/vpn/site-connections", json=conn_body, headers=HEADERS_ADMIN)
    assert res.status_code == 201, f"VPN Site connection failed: {res.text}"
    conn_id = res.json()["id"]
    log(f"VPN Site Connection created: ID={conn_id}", "OK")

    # 5. Generate IPsec Config
    res = requests.post(f"{NETWORKING_URL}/vpn/site-connections/{conn_id}/config", headers=HEADERS_ADMIN)
    assert res.status_code == 200, f"Config generation failed: {res.text}"
    cfg = res.json()
    assert "conn live-site-conn-1" in cfg["ipsec_conf"]
    assert "live-vm-vpn-psk-secret-777" in cfg["ipsec_secrets"]
    log("StrongSwan IPsec config generated successfully", "OK")

    # Cleanup VPN
    requests.delete(f"{NETWORKING_URL}/vpn/site-connections/{conn_id}", headers=HEADERS_ADMIN)
    requests.delete(f"{NETWORKING_URL}/routers/{router_id}", headers=HEADERS_ADMIN)
    requests.delete(f"{NETWORKING_URL}/vpn/ipsecpolicies/{ipsec_id}", headers=HEADERS_ADMIN)
    requests.delete(f"{NETWORKING_URL}/vpn/ikepolicies/{ike_id}", headers=HEADERS_ADMIN)
    log("VPN test resources cleaned up", "OK")

def test_volume_replication():
    log("=== Testing Volume Replication & DR (Block Store API Port 8004) ===")

    # 1. Create Volume
    vol_name = f"live-dr-volume-{int(time.time())}"
    vol_body = {
        "name": vol_name,
        "size_gb": 1,
        "volume_type": "gp2"
    }
    res = requests.post(f"{BLOCK_STORE_URL}/volumes", json=vol_body, headers=HEADERS_ADMIN)
    assert res.status_code == 201, f"Volume creation failed: {res.text}"
    vol_data = res.json()
    vol_id = vol_data["id"]
    log(f"Volume created: ID={vol_id}", "OK")

    # Wait briefly for status
    time.sleep(1)

    # 2. Enable Replication
    rep_body = {"target": "dr-secondary-datacenter", "mode": "async"}
    res = requests.post(f"{BLOCK_STORE_URL}/volumes/{vol_id}/enable-replication", json=rep_body, headers=HEADERS_ADMIN)
    assert res.status_code == 200, f"Enable replication failed: {res.text}"
    rep_data = res.json()
    assert rep_data["replication_status"] == "enabled"
    log(f"Replication enabled: target={rep_data['replication_target']}", "OK")

    # 3. Sync Replication
    res = requests.post(f"{BLOCK_STORE_URL}/volumes/{vol_id}/sync-replication", headers=HEADERS_ADMIN)
    assert res.status_code == 200, f"Sync replication failed: {res.text}"
    sync_data = res.json()
    assert sync_data["status"] == "synced"
    log(f"Replication synced at {sync_data['last_replicated_at']}", "OK")

    # 4. Failover Volume
    res = requests.post(f"{BLOCK_STORE_URL}/volumes/{vol_id}/failover", headers=HEADERS_ADMIN)
    assert res.status_code == 200, f"Failover failed: {res.text}"
    fail_data = res.json()
    assert fail_data["status"] == "failed-over"
    log("Volume failover triggered successfully", "OK")

    # 5. Disable Replication & Delete Volume
    res = requests.post(f"{BLOCK_STORE_URL}/volumes/{vol_id}/disable-replication", headers=HEADERS_ADMIN)
    assert res.status_code == 200
    log("Replication disabled", "OK")

    res = requests.delete(f"{BLOCK_STORE_URL}/volumes/{vol_id}", headers=HEADERS_ADMIN)
    assert res.status_code in (200, 202)
    log("DR Volume deleted", "OK")

if __name__ == "__main__":
    try:
        test_key_manager()
        print()
        test_networking_vpnaas()
        print()
        test_volume_replication()
        print()
        log("🎉 ALL PHASE 7 LIVE VM SERVICE TESTS PASSED SUCCESSFULLY!", "OK")
    except AssertionError as e:
        log(f"ASSERTION FAILED: {e}", "FAIL")
        sys.exit(1)
    except Exception as e:
        log(f"UNEXPECTED ERROR: {e}", "FAIL")
        sys.exit(1)
