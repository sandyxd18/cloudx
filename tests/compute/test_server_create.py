from pathlib import Path

import pytest

from tests.stack import fresh_service


def test_compute_server_create_flow_mocked_worker(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "compute.sqlite"
    for key in ("IMAGE_API_URL", "NETWORKING_API_URL", "BLOCK_STORE_API_URL"):
        monkeypatch.setenv(key, "http://peer.test:1")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    compute_main = fresh_service("compute", monkeypatch, tmp_path)
    compute_main.bootstrap_flavors()

    def fake_get(url: str):
        if "/images/" in url:
            return {"id": "image-id", "name": "cirros"}
        if "/networks/" in url:
            return {"id": "network-id", "name": "private-net"}
        raise AssertionError(url)

    posted_ports = []

    def fake_post(url: str, payload: dict):
        assert payload["device_owner"] == "compute:instance"
        posted_ports.append(payload["name"])
        return {
            "id": f"port-id-{len(posted_ports)}",
            "name": payload["name"],
            "mac_address": "02:00:00:00:00:01",
            "fixed_ip": "10.10.0.2",
            "logical_port_name": payload["name"],
        }

    created_roots = []

    def fake_block_post(path: str, payload: dict):
        if path == "/internal/volumes":
            created_roots.append(payload)
            return {
                "id": f"root-vol-{len(created_roots)}",
                "name": payload["name"],
                "path": f"/volumes/root-vol-{len(created_roots)}.qcow2",
                "bootable": True,
                "status": "available",
                "image_id": payload.get("image_id"),
            }
        if path.endswith("/attach-server"):
            return {"status": "in-use", "target_device": payload.get("target_device")}
        raise AssertionError(path)

    captured = {}

    def fake_worker(path: str, payload: dict):
        captured["path"] = path
        captured["payload"] = payload
        assert payload["flavor_snapshot"]["name"] == "tiny"
        assert payload["flavor_snapshot"]["ram_mb"] == 512
        return {"libvirt_domain_name": "mini-os-vm1-12345678", "tap_name": "tap-mini-123456", "ip_address": "10.10.0.2"}

    def fake_worker_call(method: str, payload: dict, timeout: int | None = None):
        return fake_worker("/spawn", payload)

    def fake_worker_cast(method: str, payload: dict):
        fake_worker("/spawn", payload)

    monkeypatch.setattr(compute_main.clients, "api_get", fake_get)
    monkeypatch.setattr(compute_main.clients, "api_post", fake_post)
    monkeypatch.setattr(compute_main.clients, "block_post", fake_block_post)
    deleted_volumes = []
    monkeypatch.setattr(compute_main.clients, "block_delete", lambda path: deleted_volumes.append(path))
    monkeypatch.setattr(compute_main.clients, "call_worker", fake_worker_call)
    monkeypatch.setattr(compute_main.clients, "cast_to_worker", fake_worker_cast)
    monkeypatch.setattr(compute_main.clients, "api_delete", lambda url, payload=None: None)
    released_servers = []
    monkeypatch.setattr(compute_main.clients, "release_server_volumes", lambda server_id: released_servers.append(server_id) or {"detached": []})

    db = compute_main.SessionLocal()
    dummy_user = {"project_id": "proj-1", "role": "admin"}
    server = compute_main.create_server(compute_main.ServerIn(name="vm1", image="cirros", flavor="tiny", network="private-net"), dummy_user, db)
    assert server["status"] in ("BUILD", "ACTIVE")
    assert server["backend"] == "libvirt"
    assert server["flavor_name"] == "tiny"
    assert server["vcpus"] == 1
    assert server["root_volume_id"] == "root-vol-1"
    assert server["boot_source"] == "image"
    assert captured["path"].endswith("/spawn")
    assert captured["payload"]["root_volume"]["path"] == "/volumes/root-vol-1.qcow2"
    assert isinstance(captured["payload"]["created_at"], str)

    deleted = compute_main.delete_server("vm1", dummy_user, db)
    assert deleted["name"] == "vm1"
    assert deleted["status"] in ("deleting", "DELETING")
    db.execute(compute_main.text("DELETE FROM compute_servers WHERE name = 'vm1'"))
    db.commit()
    recreated = compute_main.create_server(compute_main.ServerIn(name="vm1", image="cirros", flavor="tiny", network="private-net"), dummy_user, db)
    assert recreated["status"] in ("BUILD", "ACTIVE")
    assert recreated["name"] == "vm1"
    assert recreated["root_volume_id"] == "root-vol-2"
    assert posted_ports[0].startswith("vm1-")
    assert posted_ports[0].endswith("-port")
    assert posted_ports[1].startswith("vm1-")
    assert posted_ports[1].endswith("-port")
    assert posted_ports[0] != posted_ports[1]

    with pytest.raises(compute_main.HTTPException) as exc_info:
        compute_main.create_server(compute_main.ServerIn(name="vm2", image="cirros", flavor="tiny", network="private-net", backend="simulator"), dummy_user, db)
    assert exc_info.value.status_code == 400
    assert "simulator backend has been removed" in exc_info.value.detail
    db.close()
