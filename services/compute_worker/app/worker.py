import shutil
from pathlib import Path

import requests

from . import cloudinit, image_manager, libvirt_driver, network_manager
from .config import IMAGE_API_URL, INSTANCE_DIR, OVS_BRIDGE
from .utils import run, tool_available


def fetch_image(image_ref: str) -> dict:
    response = requests.get(f"{IMAGE_API_URL}/images/{image_ref}", timeout=15)
    response.raise_for_status()
    return response.json()


def expose_to_host_libvirt(*paths: Path) -> None:
    for path in paths:
        if not path.exists():
            continue
        path.chmod(0o666 if path.is_file() else 0o755)
        for parent in path.parents:
            if parent == parent.parent or str(parent) == "/var/lib":
                break
            if str(parent).startswith("/var/lib/mini-openstack"):
                parent.chmod(0o755)


def ensure_host_ready() -> None:
    if not Path("/dev/kvm").exists():
        raise RuntimeError("/dev/kvm is required for real libvirt compute")
    if not Path("/dev/net/tun").exists():
        raise RuntimeError("/dev/net/tun is required to create VM tap interfaces")
    if not tool_available("ovs-vsctl"):
        raise RuntimeError("ovs-vsctl is required to bind VM tap interfaces to OVS")
    if run(["ovs-vsctl", "br-exists", OVS_BRIDGE], check=False).returncode != 0:
        raise RuntimeError(f"OVS bridge {OVS_BRIDGE} was not found; run scripts/prepare-host-libvirt.sh --create-br-int")


def spawn_server(server_id: str, payload: dict) -> dict:
    ensure_host_ready()
    flavor = payload.get("flavor_snapshot") or {
        "name": payload.get("flavor_name") or payload.get("flavor"),
        "vcpus": payload.get("vcpus"),
        "ram_mb": payload.get("ram_mb"),
        "disk_gb": payload.get("disk_gb"),
    }
    missing = [key for key in ("vcpus", "ram_mb", "disk_gb") if not flavor.get(key)]
    if missing:
        raise ValueError(f"server payload missing flavor snapshot fields: {', '.join(missing)}")
    server_name = payload["name"]
    image = fetch_image(payload["image_id"])
    port = payload["port"]
    logical_port_name = port.get("logical_port_name") or port.get("name")
    mac_address = payload.get("mac_address") or port["mac_address"]
    fixed_ip = port.get("fixed_ip") or port.get("ip_address")
    tap = network_manager.tap_name(server_id)
    instance_dir = INSTANCE_DIR / server_id
    domain = libvirt_driver.domain_name(server_name, server_id)
    try:
        instance_dir.mkdir(parents=True, exist_ok=True)
        base_image = image_manager.download_image(image)
        disk_path = image_manager.create_overlay(base_image, instance_dir / "disk.qcow2", int(flavor["disk_gb"]))
        seed_iso = cloudinit.create_config_drive(instance_dir, server_id, server_name, payload.get("public_key_snapshot"), port, payload.get("user_data"))
        expose_to_host_libvirt(instance_dir, base_image, disk_path, seed_iso)
        network_manager.create_tap(tap)
        network_manager.bind_tap_to_ovs(tap, logical_port_name, mac_address, server_id)
        xml = libvirt_driver.domain_xml(domain, int(flavor["ram_mb"]), int(flavor["vcpus"]), disk_path, seed_iso, tap, mac_address)
        (instance_dir / "domain.xml").write_text(xml, encoding="utf-8")
        libvirt_driver.define_and_start(xml)
    except Exception:
        try:
            libvirt_driver.delete(domain)
        except Exception:
            pass
        network_manager.delete_tap(tap)
        if instance_dir.exists() and instance_dir.is_dir():
            shutil.rmtree(instance_dir)
        raise
    return {
        "host": "single-node",
        "instance_dir": str(instance_dir),
        "disk_path": str(disk_path),
        "config_drive_path": str(seed_iso),
        "libvirt_domain_name": domain,
        "tap_name": tap,
        "mac_address": mac_address,
        "ip_address": fixed_ip,
    }


def start_server(payload: dict) -> dict:
    libvirt_driver.start(payload["libvirt_domain_name"])
    return {"status": "ACTIVE"}


def stop_server(payload: dict) -> dict:
    libvirt_driver.stop(payload["libvirt_domain_name"])
    return {"status": "SHUTOFF"}


def reboot_server(payload: dict) -> dict:
    libvirt_driver.reboot(payload["libvirt_domain_name"])
    return {"status": "ACTIVE"}


def delete_server(server_id: str, payload: dict) -> dict:
    domain = payload.get("libvirt_domain_name")
    tap = payload.get("tap_name") or network_manager.tap_name(server_id)
    if domain:
        libvirt_driver.delete(domain)
    network_manager.delete_tap(tap)
    instance_dir = Path(payload.get("instance_dir") or INSTANCE_DIR / server_id)
    if instance_dir.exists() and instance_dir.is_dir():
        shutil.rmtree(instance_dir)
    return {"status": "deleted", "id": server_id}
