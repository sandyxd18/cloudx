from __future__ import annotations

import os
import uuid

import requests

NETWORKING_URL = os.getenv("NETWORKING_API_URL", "http://127.0.0.1:8001")

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
