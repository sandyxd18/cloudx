"""`ipv6` on a create request asks for the IPv6 address of every network too; without it a dual-stack network gives IPv4 only."""
from pathlib import Path

import pytest
from fastapi import HTTPException

from tests.stack import fresh_service

SUBNETS = {"sub4": {"id": "sub4", "ip_version": 4}, "sub6": {"id": "sub6", "ip_version": 6}, "sub6b": {"id": "sub6b", "ip_version": 6}}
NETWORKS = {
    "dual": {"id": "net-dual", "name": "dual", "subnets": ["sub4", "sub6", "sub6b"]},
    "v6only": {"id": "net-v6", "name": "v6only", "subnets": ["sub6b"]},
    "v4only": {"id": "net-v4", "name": "v4only", "subnets": ["sub4"]},
}


@pytest.fixture
def cloud(monkeypatch, tmp_path: Path):
    for key in ("IMAGE_API_URL", "NETWORKING_API_URL", "BLOCK_STORE_API_URL"):
        monkeypatch.setenv(key, "http://peer.test:1")
    compute = fresh_service("compute", monkeypatch, tmp_path)
    compute.bootstrap_flavors()
    seen = {"ports": [], "volumes": []}

    def fake_get(url):
        if "/images/" in url:
            return {"id": "image-id", "name": "cirros"}
        if "/subnets/" in url:
            return SUBNETS[url.rsplit("/", 1)[1]]
        return NETWORKS[url.rsplit("/", 1)[1]]

    def fake_post(url, payload):
        seen["ports"].append(payload)
        return {"id": f"port-{len(seen['ports'])}", "name": payload["name"], "mac_address": "02:00:00:00:00:01", "fixed_ip": "10.0.0.5",
                "ip_address": "10.0.0.5", "logical_port_name": payload["name"]}

    def fake_block_post(path, payload):
        seen["volumes"].append(path)
        if path == "/internal/volumes":
            return {"id": "root-vol", "name": payload["name"], "path": "/volumes/root.qcow2", "bootable": True, "status": "available"}
        return {"status": "in-use"}

    monkeypatch.setattr(compute.clients, "api_get", fake_get)
    monkeypatch.setattr(compute.clients, "api_post", fake_post)
    monkeypatch.setattr(compute.clients, "block_post", fake_block_post)
    monkeypatch.setattr(compute.clients, "cast_to_worker", lambda method, payload: None)
    monkeypatch.setattr(compute.clients, "api_delete", lambda url, payload=None: None)

    def create(network, **extra):
        return compute.create_server(compute.ServerIn(name="vm1", image="cirros", flavor="tiny", network=network, **extra),
                                     {"project_id": "proj-1", "role": "admin"}, compute.SessionLocal())

    return create, seen


def test_ipv6_asks_for_the_first_ipv6_subnet_of_the_network(cloud):
    create, seen = cloud
    create("dual", ipv6=True)
    assert seen["ports"][0]["fixed_ips"] == [{"subnet_id": "sub6"}]      # the IPv4 address still comes from the first IPv4 subnet


def test_without_ipv6_the_port_request_is_unchanged(cloud):
    create, seen = cloud
    create("dual")
    assert "fixed_ips" not in seen["ports"][0]


def test_ipv6_on_an_ipv6_only_network_names_its_subnet(cloud):
    create, seen = cloud
    create("v6only", ipv6=True)
    assert seen["ports"][0]["fixed_ips"] == [{"subnet_id": "sub6b"}]


def test_ipv6_applies_to_every_network_of_the_server(cloud):
    create, seen = cloud
    create("dual", networks=["v6only"], ipv6=True)
    assert [p["fixed_ips"] for p in seen["ports"]] == [[{"subnet_id": "sub6"}], [{"subnet_id": "sub6b"}]]


def test_ipv6_on_a_network_without_an_ipv6_subnet_is_refused_before_anything_is_built(cloud):
    create, seen = cloud
    with pytest.raises(HTTPException) as refused:
        create("v4only", ipv6=True)
    assert refused.value.status_code == 400 and "v4only" in refused.value.detail and "IPv6" in refused.value.detail
    assert seen["ports"] == [] and seen["volumes"] == []      # no port, and no root volume to import the image into
