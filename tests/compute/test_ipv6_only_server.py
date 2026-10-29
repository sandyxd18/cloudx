from pathlib import Path

from tests.stack import fresh_service


def test_a_server_on_an_ipv6_only_port_shows_its_ipv6_address(monkeypatch, tmp_path: Path):
    for key in ("IMAGE_API_URL", "NETWORKING_API_URL", "BLOCK_STORE_API_URL"):
        monkeypatch.setenv(key, "http://peer.test:1")
    compute = fresh_service("compute", monkeypatch, tmp_path)
    compute.bootstrap_flavors()
    casts = []

    def fake_get(url):
        if "/images/" in url:
            return {"id": "image-id", "name": "cirros"}
        return {"id": "network-id", "name": "v6only"}

    def fake_post(url, payload):
        # what networking-api returns for a port on a network with only an IPv6 subnet
        return {"id": "port-1", "name": payload["name"], "mac_address": "02:00:00:00:00:01", "fixed_ip": None,
                "ip_address": None, "ipv6_address": "fd42:7:7::2", "ipv6_cidr": "fd42:7:7::/64", "logical_port_name": payload["name"]}

    def fake_block_post(path, payload):
        if path == "/internal/volumes":
            return {"id": "root-vol", "name": payload["name"], "path": "/volumes/root.qcow2", "bootable": True, "status": "available", "image_id": payload.get("image_id")}
        return {"status": "in-use"}

    monkeypatch.setattr(compute.clients, "api_get", fake_get)
    monkeypatch.setattr(compute.clients, "api_post", fake_post)
    monkeypatch.setattr(compute.clients, "block_post", fake_block_post)
    monkeypatch.setattr(compute.clients, "cast_to_worker", lambda method, payload: casts.append((method, payload)))
    monkeypatch.setattr(compute.clients, "api_delete", lambda url, payload=None: None)

    server = compute.create_server(compute.ServerIn(name="vm6", image="cirros", flavor="tiny", network="v6only"),
                                   {"project_id": "proj-1", "role": "admin"}, compute.SessionLocal())
    assert server["ip_address"] == "fd42:7:7::2"
    method, payload = casts[0]
    assert method == "build_instance" and payload["port"]["ipv6_address"] == "fd42:7:7::2"   # the worker needs the whole port
