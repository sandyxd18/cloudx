import os
import time
import uuid
from pathlib import Path

import pytest
import requests

NETWORKING = os.getenv("NETWORKING_API_URL", "http://localhost:8001")

IMAGE = os.getenv("IMAGE_API_URL", "http://localhost:8002")

COMPUTE = os.getenv("COMPUTE_API_URL", "http://localhost:8003")

BLOCK_STORE = os.getenv("BLOCK_STORE_API_URL", "http://localhost:8004")

try:
    from jose import jwt
    AUTH_SECRET = os.environ["AUTH_SECRET_KEY"]
    TEST_TOKEN = jwt.encode(
        {"sub": "test-admin", "project_id": "admin-project", "role": "admin"},
        AUTH_SECRET,
        algorithm="HS256"
    )
    AUTH_HEADERS = {"Authorization": f"Bearer {TEST_TOKEN}"}
except Exception:
    AUTH_HEADERS = {}

def wait_json(url: str):
    last_error = None
    for _ in range(5):
        try:
            response = requests.get(url, headers=AUTH_HEADERS, timeout=3)
            if response.ok:
                return response.json()
            last_error = response.text
        except requests.RequestException as exc:
            last_error = str(exc)
        time.sleep(1)
    pytest.skip(f"service not reachable at {url}: {last_error}")

def post_json(url: str, payload: dict):
    response = requests.post(url, json=payload, headers=AUTH_HEADERS, timeout=20)
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
    items = requests.get(f"{NETWORKING}/networks", headers=AUTH_HEADERS, timeout=10).json()["items"]
    assert any(item["name"] == name for item in items)
    requests.delete(f"{NETWORKING}/networks/{created['id']}", headers=AUTH_HEADERS, timeout=10)

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
            headers=AUTH_HEADERS,
            timeout=20,
        )
    assert response.status_code < 400, response.text
    payload = response.json()
    assert payload["name"] == name
    assert payload["storage_backend"] == "file"
    assert payload["object_name"]
    assert payload["size"] == len(b"mini-openstack-test-image")
    items = requests.get(f"{IMAGE}/images", headers=AUTH_HEADERS, timeout=10).json()["items"]
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
            headers=AUTH_HEADERS,
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
            headers=AUTH_HEADERS,
            timeout=20,
        )
    assert response.status_code < 400, response.text
    created = post_json(f"{COMPUTE}/servers", {"name": server_name, "image": image_name, "flavor": "small", "network": network_name})
    assert created["status"] == "ACTIVE"
    items = requests.get(f"{COMPUTE}/servers", headers=AUTH_HEADERS, timeout=10).json()["items"]
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
    items = requests.get(f"{COMPUTE}/keypairs", headers=AUTH_HEADERS, timeout=10).json()["items"]
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
            headers=AUTH_HEADERS,
            timeout=20,
        )
    assert response.status_code < 400, response.text
    post_json(f"{COMPUTE}/keypairs", {"name": key_name})

    bad = requests.post(
        f"{COMPUTE}/servers",
        json={"name": f"{server_name}-bad", "image": image_name, "flavor": "small", "network": network_name, "key_name": "missing-keypair"},
        headers=AUTH_HEADERS,
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
    shown = requests.get(f"{COMPUTE}/flavors/{name}", headers=AUTH_HEADERS, timeout=10).json()
    assert shown["disk_gb"] == 7
    items = requests.get(f"{COMPUTE}/flavors", headers=AUTH_HEADERS, timeout=10).json()["items"]
    assert any(item["name"] == name for item in items)
    deleted = requests.delete(f"{COMPUTE}/flavors/{name}", headers=AUTH_HEADERS, timeout=10)
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
        {"name": port_name, "network": network_name, "device_id": "server-id", "device_owner": "compute:instance"},
    )
    assert created["fixed_ip"] == "10.55.0.2"
    assert created["device_owner"] == "compute:instance"
    assert created["logical_port_name"] == port_name
    assert created["gateway_ip"] == "10.55.0.1"
    assert created["dns_nameservers"] == ["1.1.1.1"]
    requests.delete(f"{NETWORKING}/ports/{port_name}", headers=AUTH_HEADERS, timeout=10)
    requests.delete(f"{NETWORKING}/subnets/{subnet_name}", headers=AUTH_HEADERS, timeout=10)
    requests.delete(f"{NETWORKING}/networks/{network_name}", headers=AUTH_HEADERS, timeout=10)

@pytest.fixture
def volume_type():
    """Creating a volume needs at least one volume type: use an existing one, else a throwaway."""
    existing = wait_json(f"{BLOCK_STORE}/volume-types")
    if existing.get("items"):
        yield None
        return
    created = post_json(f"{BLOCK_STORE}/volume-types", {"name": f"pytest-vt-{uuid.uuid4().hex[:6]}", "backend": "lvm"})
    yield created["name"]
    requests.delete(f"{BLOCK_STORE}/volume-types/{created['name']}", headers=AUTH_HEADERS, timeout=10)


def test_create_list_volume(volume_type):
    wait_json(f"{BLOCK_STORE}/health")
    name = f"pytest-vol-{uuid.uuid4().hex[:8]}"
    payload = {"name": name, "size_gb": 1, **({"volume_type": volume_type} if volume_type else {})}
    created = post_json(f"{BLOCK_STORE}/volumes", payload)
    try:
        assert created["name"] == name
        assert created["status"] in ("available", "creating")
        res = requests.get(f"{BLOCK_STORE}/volumes", headers=AUTH_HEADERS, timeout=10).json()
        items = res.get("items") or res.get("volumes") or (res if isinstance(res, list) else [])
        assert any(item["name"] == name for item in items)
    finally:
        requests.delete(f"{BLOCK_STORE}/volumes/{created['id']}", headers=AUTH_HEADERS, timeout=20)
