import os
import importlib
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

NETWORKING = os.getenv("NETWORKING_API_URL", "http://localhost:8001")
IMAGE = os.getenv("IMAGE_API_URL", "http://localhost:8002")
COMPUTE = os.getenv("COMPUTE_API_URL", "http://localhost:8003")
BLOCK_STORE = os.getenv("BLOCK_STORE_API_URL", "http://localhost:8004")


def wait_json(url: str):
    last_error = None
    for _ in range(5):
        try:
            response = requests.get(url, timeout=3)
            if response.ok:
                return response.json()
            last_error = response.text
        except requests.RequestException as exc:
            last_error = str(exc)
        time.sleep(1)
    pytest.skip(f"service not reachable at {url}: {last_error}")


def post_json(url: str, payload: dict):
    response = requests.post(url, json=payload, timeout=20)
    assert response.status_code < 400, response.text
    return response.json()


def test_health_checks():
    assert wait_json(f"{NETWORKING}/health")["status"] == "ok"
    assert wait_json(f"{IMAGE}/health")["status"] == "ok"
    assert wait_json(f"{COMPUTE}/health")["status"] == "ok"
    assert wait_json(f"{BLOCK_STORE}/health")["status"] == "ok"


def test_create_list_network():
    wait_json(f"{NETWORKING}/health")
    name = f"pytest-net-{uuid.uuid4().hex[:8]}"
    created = post_json(f"{NETWORKING}/networks", {"name": name})
    assert created["name"] == name
    items = requests.get(f"{NETWORKING}/networks", timeout=10).json()["items"]
    assert any(item["name"] == name for item in items)


def test_upload_list_image(tmp_path: Path):
    wait_json(f"{IMAGE}/health")
    image_file = tmp_path / "tiny.qcow2"
    image_file.write_bytes(b"mini-openstack-test-image")
    name = f"pytest-image-{uuid.uuid4().hex[:8]}"
    with image_file.open("rb") as handle:
        response = requests.post(
            f"{IMAGE}/images",
            data={"name": name, "disk_format": "qcow2", "container_format": "bare"},
            files={"file": ("tiny.qcow2", handle)},
            timeout=20,
        )
    assert response.status_code < 400, response.text
    payload = response.json()
    assert payload["name"] == name
    assert payload["storage_backend"] == "file"
    assert payload["object_name"]
    assert payload["size"] == len(b"mini-openstack-test-image")
    items = requests.get(f"{IMAGE}/images", timeout=10).json()["items"]
    assert any(item["name"] == name for item in items)


def test_upload_image_minio_backend_if_enabled(tmp_path: Path):
    if os.getenv("RUN_MINIO_TESTS") != "1":
        pytest.skip("set RUN_MINIO_TESTS=1 with IMAGE_STORAGE_BACKEND=minio to run MinIO upload test")
    health = wait_json(f"{IMAGE}/health")
    if health.get("storage_backend") != "minio":
        pytest.skip("image-api is not running with MinIO backend")
    image_file = tmp_path / "tiny-minio.qcow2"
    image_file.write_bytes(b"mini-openstack-minio-image")
    name = f"pytest-minio-image-{uuid.uuid4().hex[:8]}"
    with image_file.open("rb") as handle:
        response = requests.post(
            f"{IMAGE}/images",
            data={"name": name, "disk_format": "qcow2", "container_format": "bare"},
            files={"file": ("tiny-minio.qcow2", handle)},
            timeout=30,
        )
    assert response.status_code < 400, response.text
    payload = response.json()
    assert payload["storage_backend"] == "minio"
    assert payload["object_name"]


def test_create_list_server(tmp_path: Path):
    if os.getenv("RUN_REAL_COMPUTE_API_TESTS") != "1":
        pytest.skip("real compute server API test needs compute-worker and KVM; mocked flow is covered separately")
    wait_json(f"{COMPUTE}/health")
    network_name = f"pytest-server-net-{uuid.uuid4().hex[:8]}"
    image_name = f"pytest-server-image-{uuid.uuid4().hex[:8]}"
    server_name = f"pytest-server-{uuid.uuid4().hex[:8]}"
    post_json(f"{NETWORKING}/networks", {"name": network_name})
    image_file = tmp_path / "server.qcow2"
    image_file.write_bytes(b"server-image")
    with image_file.open("rb") as handle:
        response = requests.post(
            f"{IMAGE}/images",
            data={"name": image_name, "disk_format": "qcow2", "container_format": "bare"},
            files={"file": ("server.qcow2", handle)},
            timeout=20,
        )
    assert response.status_code < 400, response.text
    created = post_json(f"{COMPUTE}/servers", {"name": server_name, "image": image_name, "flavor": "small", "network": network_name})
    assert created["status"] == "ACTIVE"
    items = requests.get(f"{COMPUTE}/servers", timeout=10).json()["items"]
    assert any(item["name"] == server_name for item in items)


def test_compute_keypair_create_import_list():
    wait_json(f"{COMPUTE}/health")
    generated_name = f"pytest-key-{uuid.uuid4().hex[:8]}"
    created = post_json(f"{COMPUTE}/keypairs", {"name": generated_name})
    assert created["name"] == generated_name
    assert created["public_key"].startswith("ssh-rsa ")
    assert created["fingerprint"].startswith("SHA256:")
    assert "private_key" in created

    imported_name = f"pytest-key-import-{uuid.uuid4().hex[:8]}"
    imported = post_json(f"{COMPUTE}/keypairs/import", {"name": imported_name, "public_key": created["public_key"]})
    assert imported["name"] == imported_name
    assert imported["fingerprint"] == created["fingerprint"]
    items = requests.get(f"{COMPUTE}/keypairs", timeout=10).json()["items"]
    assert any(item["name"] == generated_name for item in items)
    assert any(item["name"] == imported_name for item in items)


def test_create_server_with_keypair_and_invalid_key(tmp_path: Path):
    if os.getenv("RUN_REAL_COMPUTE_API_TESTS") != "1":
        pytest.skip("real compute server API test needs compute-worker and KVM; mocked flow is covered separately")
    wait_json(f"{COMPUTE}/health")
    network_name = f"pytest-key-server-net-{uuid.uuid4().hex[:8]}"
    image_name = f"pytest-key-server-image-{uuid.uuid4().hex[:8]}"
    server_name = f"pytest-key-server-{uuid.uuid4().hex[:8]}"
    key_name = f"pytest-server-key-{uuid.uuid4().hex[:8]}"
    post_json(f"{NETWORKING}/networks", {"name": network_name})
    image_file = tmp_path / "server-key.qcow2"
    image_file.write_bytes(b"server-image-key")
    with image_file.open("rb") as handle:
        response = requests.post(
            f"{IMAGE}/images",
            data={"name": image_name, "disk_format": "qcow2", "container_format": "bare"},
            files={"file": ("server-key.qcow2", handle)},
            timeout=20,
        )
    assert response.status_code < 400, response.text
    post_json(f"{COMPUTE}/keypairs", {"name": key_name})

    bad = requests.post(
        f"{COMPUTE}/servers",
        json={"name": f"{server_name}-bad", "image": image_name, "flavor": "small", "network": network_name, "key_name": "missing-keypair"},
        timeout=20,
    )
    assert bad.status_code == 404

    created = post_json(f"{COMPUTE}/servers", {"name": server_name, "image": image_name, "flavor": "small", "network": network_name, "key_name": key_name})
    assert created["status"] == "ACTIVE"
    assert created["keypair_name"] == key_name
    assert created["public_key_snapshot"].startswith("ssh-rsa ")


def test_compute_flavor_crud():
    wait_json(f"{COMPUTE}/health")
    name = f"pytest-flavor-{uuid.uuid4().hex[:8]}"
    created = post_json(f"{COMPUTE}/flavors", {"name": name, "vcpus": 1, "ram_mb": 768, "disk_gb": 7})
    assert created["name"] == name
    assert created["vcpus"] == 1
    assert created["ram_mb"] == 768
    shown = requests.get(f"{COMPUTE}/flavors/{name}", timeout=10).json()
    assert shown["disk_gb"] == 7
    items = requests.get(f"{COMPUTE}/flavors", timeout=10).json()["items"]
    assert any(item["name"] == name for item in items)
    deleted = requests.delete(f"{COMPUTE}/flavors/{name}", timeout=10)
    assert deleted.status_code < 400, deleted.text


def test_networking_ipam_allocation():
    wait_json(f"{NETWORKING}/health")
    suffix = uuid.uuid4().hex[:8]
    network_name = f"pytest-ipam-net-{suffix}"
    subnet_name = f"pytest-ipam-subnet-{suffix}"
    port_name = f"pytest-ipam-port-{suffix}"
    post_json(f"{NETWORKING}/networks", {"name": network_name})
    post_json(
        f"{NETWORKING}/subnets",
        {
            "name": subnet_name,
            "network": network_name,
            "cidr": "10.55.0.0/29",
            "gateway": "10.55.0.1",
            "allocation_start": "10.55.0.2",
            "allocation_end": "10.55.0.5",
            "dns_nameservers": ["1.1.1.1"],
        },
    )
    created = post_json(
        f"{NETWORKING}/ports",
        {"name": port_name, "network": network_name, "device_id": "server-id", "device_owner": "compute:nova-like"},
    )
    assert created["fixed_ip"] == "10.55.0.2"
    assert created["device_owner"] == "compute:nova-like"
    assert created["logical_port_name"] == port_name
    assert created["gateway_ip"] == "10.55.0.1"
    assert created["dns_nameservers"] == ["1.1.1.1"]
    requests.delete(f"{NETWORKING}/ports/{port_name}", timeout=10)
    requests.delete(f"{NETWORKING}/subnets/{subnet_name}", timeout=10)
    requests.delete(f"{NETWORKING}/networks/{network_name}", timeout=10)


def test_compute_server_create_flow_mocked_worker(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "compute.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    import services.compute.app.main as compute_main

    compute_main = importlib.reload(compute_main)
    compute_main.Base.metadata.create_all(bind=compute_main.engine)
    compute_main.bootstrap_flavors()

    def fake_get(url: str):
        if "/images/" in url:
            return {"id": "image-id", "name": "cirros"}
        if "/networks/" in url:
            return {"id": "network-id", "name": "private-net"}
        raise AssertionError(url)

    posted_ports = []

    def fake_post(url: str, payload: dict):
        assert payload["device_owner"] == "compute:nova-like"
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

    monkeypatch.setattr(compute_main, "api_get", fake_get)
    monkeypatch.setattr(compute_main, "api_post", fake_post)
    monkeypatch.setattr(compute_main, "block_post", fake_block_post)
    deleted_volumes = []
    monkeypatch.setattr(compute_main, "block_delete", lambda path: deleted_volumes.append(path))
    monkeypatch.setattr(compute_main, "worker_post", fake_worker)
    monkeypatch.setattr(compute_main, "worker_delete", lambda path, payload: {"status": "deleted"})
    monkeypatch.setattr(compute_main, "api_delete", lambda url, payload=None: None)
    released_servers = []
    monkeypatch.setattr(compute_main, "release_server_volumes", lambda server_id: released_servers.append(server_id) or {"detached": []})

    db = compute_main.SessionLocal()
    server = compute_main.create_server(compute_main.ServerIn(name="vm1", image="cirros", flavor="tiny", network="private-net"), db)
    assert server["status"] == "ACTIVE"
    assert server["backend"] == "libvirt"
    assert server["flavor_name"] == "tiny"
    assert server["vcpus"] == 1
    assert server["root_volume_id"] == "root-vol-1"
    assert server["boot_source"] == "image"
    assert captured["path"].endswith("/spawn")
    assert captured["payload"]["root_volume"]["path"] == "/volumes/root-vol-1.qcow2"
    assert isinstance(captured["payload"]["created_at"], str)

    deleted = compute_main.delete_server("vm1", db)
    assert deleted["name"] == "vm1"
    assert deleted["root_volume_deleted"] is True
    assert released_servers == [server["id"]]
    assert deleted_volumes == ["/volumes/root-vol-1"]
    recreated = compute_main.create_server(compute_main.ServerIn(name="vm1", image="cirros", flavor="tiny", network="private-net"), db)
    assert recreated["status"] == "ACTIVE"
    assert recreated["name"] == "vm1"
    assert recreated["root_volume_id"] == "root-vol-2"
    assert posted_ports[0].startswith("vm1-")
    assert posted_ports[0].endswith("-port")
    assert posted_ports[1].startswith("vm1-")
    assert posted_ports[1].endswith("-port")
    assert posted_ports[0] != posted_ports[1]

    with pytest.raises(compute_main.HTTPException) as exc_info:
        compute_main.create_server(compute_main.ServerIn(name="vm2", image="cirros", flavor="tiny", network="private-net", backend="simulator"), db)
    assert exc_info.value.status_code == 400
    assert "simulator backend has been removed" in exc_info.value.detail
    db.close()


def test_block_store_detach_server_volumes(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "block.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("VOLUME_STORE_PATH", str(tmp_path / "volumes"))
    import services.block_store.app.main as block_main

    block_main = importlib.reload(block_main)
    block_main.Base.metadata.create_all(bind=block_main.engine)
    db = block_main.SessionLocal()
    volume = block_main.Volume(
        id="vol-id",
        name="vol1",
        size_gb=1,
        status="in-use",
        attached_to_server_id="server-id",
        path=str(tmp_path / "volumes" / "vol-id.qcow2"),
    )
    db.add(volume)
    db.commit()

    result = block_main.detach_server_volumes("server-id", db)
    assert len(result["detached"]) == 1
    refreshed = block_main.require_volume(db, "vol1")
    assert refreshed.status == "available"
    assert refreshed.attached_to_server_id is None
    assert refreshed.target_device is None

    refreshed.status = "in-use"
    refreshed.attached_to_server_id = "deleted-server-id"
    db.commit()
    forced = block_main.force_detach_volume("vol1", db)
    assert forced["status"] == "available"
    assert forced["attached_to_server_id"] is None
    db.close()


def test_block_store_snapshot_and_volume_from_snapshot(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "block-snap.sqlite"
    volume_root = tmp_path / "volumes"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("VOLUME_STORE_PATH", str(volume_root))
    monkeypatch.setenv("SNAPSHOT_STORE_PATH", str(volume_root / "snapshots"))
    import services.block_store.app.main as block_main

    block_main = importlib.reload(block_main)
    block_main.Base.metadata.create_all(bind=block_main.engine)
    block_main.VOLUME_STORE_PATH.mkdir(parents=True, exist_ok=True)
    block_main.SNAPSHOT_STORE_PATH.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(block_main, "qemu_img_available", lambda: False)
    db = block_main.SessionLocal()
    volume_path = volume_root / "vol-id.qcow2"
    volume_path.write_bytes(b"volume-bytes")
    volume = block_main.Volume(id="vol-id", name="vol1", size_gb=1, status="available", path=str(volume_path), bootable=True)
    db.add(volume)
    db.commit()

    snap = block_main.create_volume_snapshot("vol1", block_main.SnapshotIn(name="snap1"), db)
    assert snap["name"] == "snap1"
    assert Path(snap["path"]).read_bytes() == b"volume-bytes"

    created = block_main.create_volume(block_main.VolumeIn(name="vol2", size_gb=1, snapshot="snap1"), db)
    assert created["snapshot_id"] == snap["id"]
    assert created["source_type"] == "snapshot"
    assert Path(created["path"]).read_bytes() == b"volume-bytes"
    db.close()


def test_create_list_volume():
    wait_json(f"{BLOCK_STORE}/health")
    name = f"pytest-vol-{uuid.uuid4().hex[:8]}"
    created = post_json(f"{BLOCK_STORE}/volumes", {"name": name, "size_gb": 1})
    assert created["name"] == name
    assert created["status"] == "available"
    items = requests.get(f"{BLOCK_STORE}/volumes", timeout=10).json()["items"]
    assert any(item["name"] == name for item in items)


@pytest.mark.parametrize("command", [["networking", "-h"], ["image", "-h"], ["compute", "-h"], ["block-store", "-h"]])
def test_cli_short_help(command):
    script = Path(__file__).resolve().parents[1] / "cli" / command[0]
    result = subprocess.run([sys.executable, str(script), command[1]], text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "Usage" in result.stdout


@pytest.mark.parametrize(
    "command",
    [
        ["compute", "server", "create", "-h"],
        ["compute", "server", "console", "-h"],
        ["compute", "server", "console-url", "-h"],
        ["compute", "server", "metadata", "-h"],
        ["compute", "server", "user-data", "-h"],
        ["compute", "server", "set-user-data", "-h"],
        ["compute", "server", "logs", "-h"],
        ["compute", "server", "ssh-info", "-h"],
        ["compute", "server", "snapshot", "create", "-h"],
        ["compute", "server", "add-port", "-h"],
        ["compute", "server", "remove-port", "-h"],
        ["compute", "host", "check", "-h"],
        ["compute", "host", "show", "-h"],
        ["compute", "flavor", "create", "-h"],
        ["compute", "flavor", "list", "-h"],
    ],
)
def test_compute_cli_new_help(command):
    script = Path(__file__).resolve().parents[1] / "cli" / command[0]
    result = subprocess.run([sys.executable, str(script), *command[1:]], text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "Usage" in result.stdout


@pytest.mark.parametrize(
    "command",
    [
        ["networking", "network", "create", "-h"],
        ["networking", "subnet", "create", "-h"],
        ["networking", "router", "set-gateway", "-h"],
        ["networking", "floating-ip", "create", "-h"],
        ["networking", "floating-ip", "associate", "-h"],
        ["networking", "security-group", "create", "-h"],
        ["networking", "security-group", "rule", "create", "-h"],
        ["networking", "port", "set-security-group", "-h"],
    ],
)
def test_networking_cli_new_help(command):
    script = Path(__file__).resolve().parents[1] / "cli" / command[0]
    result = subprocess.run([sys.executable, str(script), *command[1:]], text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "Usage" in result.stdout


@pytest.mark.parametrize(
    "command",
    [
        ["block-store", "volume", "create", "-h"],
        ["block-store", "volume", "show", "-h"],
        ["block-store", "snapshot", "create", "-h"],
        ["block-store", "snapshot", "list", "-h"],
    ],
)
def test_block_store_cli_new_help(command):
    script = Path(__file__).resolve().parents[1] / "cli" / command[0]
    result = subprocess.run([sys.executable, str(script), *command[1:]], text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "Usage" in result.stdout


def test_network_create_help_has_no_provider_nic_options():
    script = Path(__file__).resolve().parents[1] / "cli" / "networking"
    result = subprocess.run([sys.executable, str(script), "network", "create", "-h"], text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "--external" in result.stdout
    assert "--provider-bridge" not in result.stdout
    assert "--provider-cidr" not in result.stdout


def test_libvirt_domain_xml_generation():
    from services.compute_worker.app.libvirt_driver import domain_xml

    xml = domain_xml(
        name="mini-os-vm1-12345678",
        memory_mb=512,
        vcpus=1,
        disk_path="/instances/1/disk.qcow2",
        seed_iso="/instances/1/seed.iso",
        tap_name="tap-mini-123456",
        mac_address="02:00:00:00:00:01",
    )
    assert "<name>mini-os-vm1-12345678</name>" in xml
    assert 'type="kvm"' in xml
    assert 'type="scsi" model="virtio-scsi"' in xml
    assert 'file="/instances/1/disk.qcow2"' in xml
    assert 'dev="sda" bus="scsi"' in xml
    assert 'type="drive" controller="0" bus="0" target="0" unit="0"' in xml
    assert 'dev="tap-mini-123456" managed="no"' in xml
    assert 'address="02:00:00:00:00:01"' in xml


def test_volume_disk_xml_uses_scsi_hotplug_bus():
    from services.compute_worker.app.libvirt_driver import disk_device_xml
    from services.compute_worker.app.volume_manager import _target_dev

    assert _target_dev("volume-id").startswith("sd")
    xml = disk_device_xml("/volumes/vol1.qcow2", "sdb", "qcow2")
    assert 'target dev="sdb" bus="scsi"' in xml
    assert 'type="drive" controller="0" bus="0" target="0" unit="1"' in xml


def test_metadata_service_lookup_by_instance_id(monkeypatch):
    from services.metadata.app import metadata_service

    captured = {}

    def fake_compute_get(path, params=None):
        captured["path"] = path
        captured["params"] = params
        return {
            "uuid": "server-id",
            "hostname": "vm1",
            "name": "vm1",
            "public_keys": {"mykey": "ssh-rsa AAAATEST"},
            "user_data": "#cloud-config\n",
        }

    class FakeRequest:
        headers = {}

    monkeypatch.setattr(metadata_service, "compute_get", fake_compute_get)
    metadata = metadata_service.lookup_metadata(FakeRequest(), instance_id="server-id")
    assert metadata["uuid"] == "server-id"
    assert captured["path"] == "/servers/metadata-lookup"
    assert captured["params"] == {"instance_id": "server-id"}
    assert metadata_service.public_keys_index(metadata) == "0=mykey\n"
    assert metadata_service.first_public_key(metadata) == "ssh-rsa AAAATEST\n"
    assert metadata_service.default_user_data({}) == "#cloud-config\npackage_update: false\n"


def test_metadata_service_network_data_format():
    from services.metadata.app import metadata_service

    payload = metadata_service.network_data_json({
        "port_id": "port-id",
        "fixed_ip": "10.10.0.5",
        "mac_address": "02:00:00:00:00:05",
        "network_id": "network-id",
        "network": {"cidr": "10.10.0.0/24", "gateway_ip": "10.10.0.1", "dns_nameservers": ["1.1.1.1"]},
    })
    assert payload["links"][0]["id"] == "port-id"
    assert payload["networks"][0]["netmask"] == "255.255.255.0"
    assert payload["networks"][0]["routes"][0]["gateway"] == "10.10.0.1"
    assert payload["services"] == [{"type": "dns", "address": "1.1.1.1"}]


def test_networking_ovn_command_builders():
    from services.networking.app import main as net_main

    dhcp = net_main.dhcp_options_args("10.10.0.0/24", "10.10.0.1", ["1.1.1.1"])
    assert dhcp[:2] == ["create", "DHCP_Options"]
    assert "options:router=10.10.0.1" in dhcp
    assert "options:dns_server={1.1.1.1}" in dhcp

    assert net_main.nat_rule_args("router1", "192.168.100.10", "10.10.0.2") == [
        "--may-exist",
        "lr-nat-add",
        "router1",
        "dnat_and_snat",
        "192.168.100.10",
        "10.10.0.2",
    ]

    rule = net_main.SecurityGroupRule(
        security_group_id="sg-id",
        direction="ingress",
        protocol="tcp",
        port_min="22",
        port_max="22",
        remote_ip_prefix="0.0.0.0/0",
    )
    match = net_main.acl_match("sg-id", rule)
    assert f"outport == @{net_main.port_group_name('sg-id')}" in match
    assert "tcp.dst == 22" in match
    assert "ip4.src == 0.0.0.0/0" in match
    assert net_main.acl_priority(rule) == "2002"

    egress = net_main.SecurityGroupRule(security_group_id="sg-id", direction="egress", protocol="any", remote_ip_prefix="0.0.0.0/0")
    assert f"inport == @{net_main.port_group_name('sg-id')}" in net_main.acl_match("sg-id", egress)

    localnet = net_main.localnet_commands("public-net", "physnet1")
    assert ["lsp-set-type", "public-net-localnet", "localnet"] in localnet
    assert ["lsp-set-options", "public-net-localnet", "network_name=physnet1"] in localnet
    assert net_main.external_network_defaults("public-net")["provider_bridge"]


def test_floating_ip_allocation_reuses_released_ip(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "networking.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    import services.networking.app.main as net_main

    net_main = importlib.reload(net_main)
    net_main.Base.metadata.create_all(bind=net_main.engine)
    db = net_main.SessionLocal()
    network = net_main.Network(
        name="public-net",
        external=True,
        provider=True,
        provider_cidr="192.168.56.0/24",
        gateway_ip="192.168.56.1",
        allocation_start="192.168.56.100",
        allocation_end="192.168.56.101",
    )
    db.add(network)
    db.commit()
    db.refresh(network)

    first = net_main.allocate_floating_ip(db, network)
    assert first == "192.168.56.100"
    db.add(net_main.FloatingIP(name="fip-1", floating_ip=first, floating_ip_address=first, external_network_id=network.id))
    db.commit()
    assert net_main.allocate_floating_ip(db, network) == "192.168.56.101"
    fip = net_main.require_floating_ip(db, first)
    db.delete(fip)
    db.commit()
    assert net_main.allocate_floating_ip(db, network) == "192.168.56.100"
    db.close()


def test_cloud_init_rendering():
    from services.compute_worker.app.cloudinit import render_meta_data, render_network_config, render_user_data

    assert render_meta_data("server-id", "vm1") == "instance-id: server-id\nlocal-hostname: vm1\n"
    user_data = render_user_data("ssh-rsa AAAATEST user@example")
    assert user_data.startswith("#cloud-config")
    assert "package_update: false" in user_data
    assert "ssh_pwauth: false" in user_data
    assert "resize_rootfs: true" in user_data
    assert "ssh_authorized_keys:" in user_data
    assert "ssh-rsa AAAATEST user@example" in user_data
    network_config = render_network_config({"fixed_ip": "10.10.0.2", "cidr": "10.10.0.0/24", "gateway_ip": "10.10.0.1", "dns_nameservers": ["1.1.1.1"]})
    assert "addresses: [10.10.0.2/24]" in network_config
    assert "gateway4: 10.10.0.1" in network_config


def test_tap_name_generation():
    from services.compute_worker.app.network_manager import tap_name

    generated = tap_name("12345678-aaaa-bbbb-cccc-000000000000")
    assert generated == "tap-mini-123456"
    assert len(generated) <= 15


def test_compute_worker_host_ready_requires_ovs_bridge(monkeypatch):
    from services.compute_worker.app import worker

    class FakeProc:
        returncode = 1
        stderr = ""
        stdout = ""

    monkeypatch.setattr(worker.Path, "exists", lambda self: True)
    monkeypatch.setattr(worker, "tool_available", lambda name: True)
    monkeypatch.setattr(worker, "run", lambda *args, **kwargs: FakeProc())

    with pytest.raises(RuntimeError) as exc_info:
        worker.ensure_host_ready()
    assert "OVS bridge" in str(exc_info.value)
    assert "--create-br-int" in str(exc_info.value)


def test_compute_worker_health_preflight_mock(monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    assert fastapi
    from services.compute_worker.app import main

    class FakeConn:
        def close(self):
            return None

    monkeypatch.setattr(main.libvirt_driver, "connect", lambda: FakeConn())
    monkeypatch.setattr(main, "tool_available", lambda name: True)

    class FakeProc:
        returncode = 0

    monkeypatch.setattr(main, "run", lambda *args, **kwargs: FakeProc())
    health = main.preflight()
    assert health["libvirt_connected"] is True
    assert health["ovs_available"] is True
    assert health["br_int_exists"] is True
    assert "tun_available" in health


def test_create_tap_reports_failed_tuntap(monkeypatch):
    from services.compute_worker.app import network_manager

    class FakeProc:
        def __init__(self, returncode=0, stderr=""):
            self.returncode = returncode
            self.stderr = stderr
            self.stdout = ""

    commands = []

    def fake_run(cmd, check=True):
        commands.append(cmd)
        if cmd[:4] == ["ip", "tuntap", "add", "dev"]:
            return FakeProc(1, "open: No such file or directory")
        return FakeProc()

    monkeypatch.setattr(network_manager, "run", fake_run)
    with pytest.raises(RuntimeError) as exc_info:
        network_manager.create_tap("tap-mini-test")
    assert "failed to create tap interface tap-mini-test" in str(exc_info.value)
    assert "/dev/net/tun" in str(exc_info.value)
    assert ["ovs-vsctl", "--if-exists", "del-port", "tap-mini-test"] in commands


def test_create_tap_verifies_visibility(monkeypatch):
    from services.compute_worker.app import network_manager

    class FakeProc:
        def __init__(self, returncode=0, stderr=""):
            self.returncode = returncode
            self.stderr = stderr
            self.stdout = ""

    def fake_run(cmd, check=True):
        if cmd[:4] == ["ip", "link", "show", "dev"]:
            return FakeProc(1, 'Device "tap-mini-test" does not exist.')
        return FakeProc()

    monkeypatch.setattr(network_manager, "run", fake_run)
    with pytest.raises(RuntimeError) as exc_info:
        network_manager.create_tap("tap-mini-test")
    assert "host network namespace" in str(exc_info.value)


def test_cleanup_scripts_dry_run():
    for script in ("cleanup-compute.sh", "cleanup-network.sh", "cleanup-all.sh", "cleanup-provider-network.sh"):
        result = subprocess.run(["bash", str(ROOT / "scripts" / script), "--dry-run"], text=True, capture_output=True, timeout=20)
        assert result.returncode == 0, result.stderr


def test_ovn_northd_start_creates_runtime_dir():
    script = ROOT / "services" / "ovn" / "start-northd.sh"
    content = script.read_text()
    assert "mkdir -p /var/run/ovn" in content
    assert "--pidfile=/var/run/ovn/ovn-northd.pid" in content
