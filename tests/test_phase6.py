"""
Test Suite — Fase 6: Multi-Node Compute & Advanced Orchestration
==================================================================

Tests:
  • Placement API multi-node scheduling & heartbeat
  • Compute API live migration endpoints
  • Networking API VPC peering & BGP peers
  • Orchestration API auto-scaling groups
"""

from __future__ import annotations

import os
import sys
import time
import uuid

import pytest
import requests

# Base URLs
PLACEMENT_URL = os.getenv("PLACEMENT_API_URL", "http://127.0.0.1:8006")
COMPUTE_URL = os.getenv("COMPUTE_API_URL", "http://127.0.0.1:8003")
NETWORKING_URL = os.getenv("NETWORKING_API_URL", "http://127.0.0.1:8001")
ORCHESTRATION_URL = os.getenv("ORCHESTRATION_API_URL", "http://127.0.0.1:8013")
AUTH_URL = os.getenv("AUTH_API_URL", "http://127.0.0.1:8005")

ADMIN_USER = os.getenv("AUTH_ADMIN_USERNAME", "admin")
ADMIN_PASS = os.getenv("AUTH_ADMIN_PASSWORD", "admin123")


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(
        f"{AUTH_URL}/auth/tokens",
        json={"username": ADMIN_USER, "password": ADMIN_PASS},
        timeout=10,
    )
    assert r.status_code in (200, 201)
    return r.json()["token"]


@pytest.fixture(scope="module")
def auth_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"}


@pytest.fixture(scope="module")
def test_router(auth_headers):
    """Create a test router for VPC peering/BGP tests."""
    r = requests.post(
        f"{NETWORKING_URL}/routers",
        headers=auth_headers,
        json={"name": f"test-router-{uuid.uuid4().hex[:6]}", "external_gateway_info": None},
        timeout=10,
    )
    if r.status_code in (200, 201):
        return r.json()
    pytest.skip("Could not create test router")


# ═══════════════════════════════════════════════════════════════════════════════
# Placement API / Multi-Node Scheduling Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestPlacementMultiNode:
    def test_health(self):
        r = requests.get(f"{PLACEMENT_URL}/health", timeout=10)
        assert r.status_code in (200, 201)

    def test_create_resource_provider(self, auth_headers):
        data = {"name": f"compute-node-{uuid.uuid4().hex[:6]}"}
        r = requests.post(
            f"{PLACEMENT_URL}/resource_providers",
            headers=auth_headers,
            json=data,
            timeout=10,
        )
        assert r.status_code == 201
        result = r.json()
        assert "uuid" in result
        assert result["name"] == data["name"]
        return result["uuid"]

    def test_set_inventories(self, auth_headers):
        # Create RP
        rp_resp = requests.post(
            f"{PLACEMENT_URL}/resource_providers",
            headers=auth_headers,
            json={"name": f"sched-test-{uuid.uuid4().hex[:6]}"},
            timeout=10,
        )
        rp_uuid = rp_resp.json()["uuid"]

        data = {
            "inventories": {
                "VCPU": {"total": 8, "reserved": 0, "allocation_ratio": 1.0},
                "MEMORY_MB": {"total": 16384, "reserved": 512, "allocation_ratio": 1.0},
                "DISK_GB": {"total": 500, "reserved": 0, "allocation_ratio": 1.0},
            }
        }
        r = requests.put(
            f"{PLACEMENT_URL}/resource_providers/{rp_uuid}/inventories",
            headers=auth_headers,
            json=data,
            timeout=10,
        )
        assert r.status_code in (200, 201)
        result = r.json()
        assert "inventories" in result
        assert result["inventories"]["VCPU"]["total"] == 8

    def test_heartbeat(self, auth_headers):
        rp_resp = requests.post(
            f"{PLACEMENT_URL}/resource_providers",
            headers=auth_headers,
            json={"name": f"heartbeat-test-{uuid.uuid4().hex[:6]}"},
            timeout=10,
        )
        rp_uuid = rp_resp.json()["uuid"]

        data = {
            "host": "compute-node-02",
            "cpu_percent_used": 25.0,
            "memory_mb_used": 4096,
            "memory_mb_total": 16384,
            "disk_gb_used": 50,
            "disk_gb_total": 500,
            "running_vms": 2,
        }
        r = requests.post(
            f"{PLACEMENT_URL}/resource_providers/{rp_uuid}/heartbeat",
            headers=auth_headers,
            json=data,
            timeout=10,
        )
        assert r.status_code in (200, 201)
        assert r.json()["resource_provider_uuid"] == rp_uuid

    def test_scheduler_select_destination(self, auth_headers):
        data = {
            "resources": {"VCPU": 2, "MEMORY_MB": 4096, "DISK_GB": 20},
            "strategy": "memory",
        }
        r = requests.post(
            f"{PLACEMENT_URL}/scheduler/select_destination",
            headers=auth_headers,
            json=data,
            timeout=10,
        )
        assert r.status_code in (200, 201)
        result = r.json()
        assert "selected_host" in result
        assert "resource_provider_uuid" in result


# ═══════════════════════════════════════════════════════════════════════════════
# Compute API Live Migration Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestComputeLiveMigration:
    def test_migration_candidates_endpoint(self, auth_headers):
        # We just test the endpoint shape; real migration requires a running VM.
        r = requests.get(
            f"{COMPUTE_URL}/servers/nonexistent/migration-candidates",
            headers=auth_headers,
            timeout=10,
        )
        # Should be 404 for nonexistent server
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# Networking API VPC Peering & BGP Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestNetworkingVPCPeering:
    def test_create_vpc_peering(self, auth_headers, test_router):
        # Create a second router
        r2 = requests.post(
            f"{NETWORKING_URL}/routers",
            headers=auth_headers,
            json={"name": f"peer-router-{uuid.uuid4().hex[:6]}"},
            timeout=10,
        )
        assert r2.status_code in (200, 201)
        peer_router = r2.json()

        data = {
            "name": f"peering-{uuid.uuid4().hex[:6]}",
            "accepter_router_id": peer_router["id"],
        }
        r = requests.post(
            f"{NETWORKING_URL}/routers/{test_router['id']}/peering",
            headers=auth_headers,
            json=data,
            timeout=10,
        )
        assert r.status_code == 201
        result = r.json()
        assert result["requester_router_id"] == test_router["id"]
        assert result["accepter_router_id"] == peer_router["id"]
        assert result["status"] == "active"

    def test_list_vpc_peerings(self, auth_headers):
        r = requests.get(
            f"{NETWORKING_URL}/vpc-peerings",
            headers=auth_headers,
            timeout=10,
        )
        assert r.status_code in (200, 201)
        assert isinstance(r.json(), list)

    def test_create_bgp_peer(self, auth_headers, test_router):
        data = {
            "name": f"bgp-peer-{uuid.uuid4().hex[:6]}",
            "router_id": test_router["id"],
            "peer_ip": "192.168.255.1",
            "remote_as": 64512,
            "local_as": 64513,
            "advertised_prefixes": ["10.0.0.0/24", "10.0.1.0/24"],
        }
        r = requests.post(
            f"{NETWORKING_URL}/bgp-peers",
            headers=auth_headers,
            json=data,
            timeout=10,
        )
        assert r.status_code == 201
        result = r.json()
        assert result["peer_ip"] == "192.168.255.1"
        assert result["remote_as"] == 64512
        assert "10.0.0.0/24" in (result["advertised_prefixes"] or [])

    def test_list_bgp_peers(self, auth_headers):
        r = requests.get(
            f"{NETWORKING_URL}/bgp-peers",
            headers=auth_headers,
            timeout=10,
        )
        assert r.status_code in (200, 201)
        assert isinstance(r.json(), list)


# ═══════════════════════════════════════════════════════════════════════════════
# Orchestration API Auto-Scaling Group Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestOrchestration:
    def test_health(self):
        r = requests.get(f"{ORCHESTRATION_URL}/health", timeout=10)
        assert r.status_code in (200, 201)
        assert r.json()["service"] == "orchestration-api"

    def test_create_scaling_group(self, auth_headers):
        data = {
            "name": f"test-asg-{uuid.uuid4().hex[:6]}",
            "description": "Test auto-scaling group",
            "min_instances": 1,
            "max_instances": 3,
            "target_cpu_percent": 70,
            "cooldown_seconds": 60,
            "launch_template_id": str(uuid.uuid4()),
            "image_id": str(uuid.uuid4()),
            "flavor_id": str(uuid.uuid4()),
            "network_id": str(uuid.uuid4()),
        }
        r = requests.post(
            f"{ORCHESTRATION_URL}/scaling-groups",
            headers=auth_headers,
            json=data,
            timeout=10,
        )
        assert r.status_code == 201
        result = r.json()
        assert result["name"] == data["name"]
        assert result["min_instances"] == 1
        assert result["max_instances"] == 3

    def test_list_scaling_groups(self, auth_headers):
        r = requests.get(
            f"{ORCHESTRATION_URL}/scaling-groups",
            headers=auth_headers,
            timeout=10,
        )
        assert r.status_code in (200, 201)
        assert isinstance(r.json(), list)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])