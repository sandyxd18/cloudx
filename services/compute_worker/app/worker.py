import logging
import shutil
from pathlib import Path

import requests

from . import cloudinit, libvirt_driver, network_manager
from .config import COMPUTE_HOST, IMAGE_API_URL, INSTANCE_DIR, OVS_BRIDGE
from shared.proc import succeeds

from .utils import tool_available

log = logging.getLogger("compute-worker")


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
            if str(parent).startswith("/var/lib/cloudx") or str(parent).startswith("/var/lib/mini-openstack"):
                parent.chmod(0o755)


def ensure_host_ready() -> None:
    if not Path("/dev/kvm").exists():
        raise RuntimeError("/dev/kvm is required for real libvirt compute")
    if not Path("/dev/net/tun").exists():
        raise RuntimeError("/dev/net/tun is required to create VM tap interfaces")
    if not tool_available("ovs-vsctl"):
        raise RuntimeError("ovs-vsctl is required to bind VM tap interfaces to OVS")
    if not succeeds(["ovs-vsctl", "br-exists", OVS_BRIDGE]):
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
    root_volume = payload.get("root_volume")
    if not root_volume or not root_volume.get("path"):
        raise ValueError("server payload missing root_volume path; compute is volume-first")
    port = payload["port"]
    logical_port_name = port.get("logical_port_name") or port.get("name")
    mac_address = payload.get("mac_address") or port["mac_address"]
    fixed_ip = port.get("fixed_ip") or port.get("ip_address")
    tap = network_manager.tap_name(server_id)
    instance_dir = INSTANCE_DIR / server_id
    domain = libvirt_driver.domain_name(server_name, server_id)
    try:
        instance_dir.mkdir(parents=True, exist_ok=True)
        disk_path = Path(root_volume["path"])
        if not str(disk_path).startswith("rbd:") and not disk_path.exists():  # an RBD image is opened by qemu over the network
            raise RuntimeError(f"root volume path does not exist: {disk_path}")
        seed_iso = cloudinit.create_config_drive(
            instance_dir,
            server_id,
            server_name,
            payload.get("public_key_snapshot"),
            port,
            payload.get("user_data"),
            payload.get("keypair_name"),
        )
        console_log = instance_dir / "console.log"
        console_log.touch(exist_ok=True)
        console_log.chmod(0o666)
        expose_to_host_libvirt(instance_dir, disk_path, seed_iso, console_log)
        network_manager.create_tap(tap)
        network_manager.bind_tap_to_ovs(tap, logical_port_name, mac_address, server_id)
        xml = libvirt_driver.domain_xml(domain, int(flavor["ram_mb"]), int(flavor["vcpus"]), disk_path, seed_iso, tap, mac_address)
        (instance_dir / "domain.xml").write_text(xml, encoding="utf-8")
        libvirt_driver.define_and_start(xml)
    except Exception:
        try:
            libvirt_driver.delete(domain)
        except Exception as exc:
            log.warning("rollback: could not delete domain %s: %s", domain, exc)
        network_manager.delete_tap(tap)
        if instance_dir.exists() and instance_dir.is_dir():
            shutil.rmtree(instance_dir)
        raise
    return {
        "host": COMPUTE_HOST,
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
    domain = payload["libvirt_domain_name"]
    reboot_type = payload.get("reboot_type", "soft")
    if reboot_type == "hard":
        # Hard reboot: destroy and start (without re-provision)
        libvirt_driver.delete(domain)
        # Re-define from saved XML if available
        instance_dir = Path(payload.get("instance_dir") or INSTANCE_DIR / domain)
        domain_xml_path = instance_dir / "domain.xml"
        if domain_xml_path.exists():
            libvirt_driver.define_and_start(domain_xml_path.read_text())
        else:
            libvirt_driver.start(domain)
    else:
        libvirt_driver.reboot(domain)
    return {"status": "ACTIVE"}


# ── New power state actions ────────────────────────────────────────────────────

def pause_server(payload: dict) -> dict:
    """Pause VM at hypervisor level (PAUSED in libvirt)."""
    libvirt_driver.pause(payload["libvirt_domain_name"])
    return {"status": "PAUSED"}


def resume_server(payload: dict) -> dict:
    """Resume from PAUSED state."""
    libvirt_driver.resume(payload["libvirt_domain_name"])
    return {"status": "ACTIVE"}


def suspend_server(payload: dict) -> dict:
    """Save VM state to disk (libvirt managed save → SUSPENDED)."""
    libvirt_driver.suspend_to_disk(payload["libvirt_domain_name"])
    return {"status": "SUSPENDED"}


def unsuspend_server(payload: dict) -> dict:
    """Restore from managed save."""
    libvirt_driver.resume_from_disk(payload["libvirt_domain_name"])
    return {"status": "ACTIVE"}


# ── Resize ─────────────────────────────────────────────────────────────────────

def resize_server(payload: dict) -> dict:
    """Live resize CPU/RAM; falls back to config-only when hotplug is unsupported."""
    domain = payload["libvirt_domain_name"]
    new_vcpus = payload.get("new_vcpus")
    new_ram_mb = payload.get("new_ram_mb")
    libvirt_driver.resize(domain, new_vcpus, new_ram_mb)
    return {"status": "resized", "vcpus": new_vcpus, "ram_mb": new_ram_mb}


# ── Rebuild ────────────────────────────────────────────────────────────────────

def rebuild_server(server_id: str, payload: dict) -> dict:
    """Replace root disk with a new image, keeping the same network config."""
    domain = payload["libvirt_domain_name"]
    tap = payload.get("tap_name") or network_manager.tap_name(server_id)
    mac_address = payload.get("mac_address", "")
    flavor = payload.get("flavor_snapshot") or {}
    ram_mb = int(payload.get("ram_mb") or flavor.get("ram_mb") or 512)
    vcpus = int(payload.get("vcpus") or flavor.get("vcpus") or 1)

    image = payload.get("image") or {}
    image_ref = image.get("id") or image.get("name") or payload.get("image_id", "")
    if not image_ref:
        raise ValueError("rebuild payload missing image.id")

    instance_dir = Path(payload.get("instance_dir") or INSTANCE_DIR / server_id)
    instance_dir.mkdir(parents=True, exist_ok=True)

    from .image_manager import download_and_prepare_disk
    new_disk_path = instance_dir / "disk-rebuild.qcow2"
    download_and_prepare_disk(image_ref, new_disk_path)
    expose_to_host_libvirt(new_disk_path)

    port = payload.get("port") or {}
    seed_iso = cloudinit.create_config_drive(
        instance_dir, server_id, payload.get("name", ""),
        payload.get("public_key_snapshot"), port, payload.get("user_data"),
    )
    expose_to_host_libvirt(seed_iso)
    libvirt_driver.rebuild(domain, str(new_disk_path), str(seed_iso), tap, mac_address, ram_mb, vcpus)
    return {"status": "ACTIVE", "disk_path": str(new_disk_path)}


# ── Rescue / Unrescue ──────────────────────────────────────────────────────────

def rescue_server(server_id: str, payload: dict) -> dict:
    """Boot from rescue image while keeping original disk intact."""
    domain = payload["libvirt_domain_name"]
    tap = payload.get("tap_name") or network_manager.tap_name(server_id)
    mac_address = payload.get("mac_address", "")
    flavor = payload.get("flavor_snapshot") or {}
    ram_mb = int(payload.get("ram_mb") or flavor.get("ram_mb") or 512)
    vcpus = int(payload.get("vcpus") or flavor.get("vcpus") or 1)

    rescue_image_id = payload.get("rescue_image_id") or payload.get("image_id")
    if not rescue_image_id:
        raise ValueError("rescue payload missing rescue_image_id")

    instance_dir = Path(payload.get("instance_dir") or INSTANCE_DIR / server_id)
    instance_dir.mkdir(parents=True, exist_ok=True)

    from .image_manager import download_and_prepare_disk
    rescue_disk = instance_dir / "disk-rescue.qcow2"
    download_and_prepare_disk(rescue_image_id, rescue_disk)
    expose_to_host_libvirt(rescue_disk)

    port = payload.get("port") or {}
    seed_iso = cloudinit.create_config_drive(
        instance_dir, server_id, payload.get("name", ""),
        payload.get("public_key_snapshot"), port, payload.get("user_data"),
    )
    expose_to_host_libvirt(seed_iso)
    libvirt_driver.rebuild(domain, str(rescue_disk), str(seed_iso), tap, mac_address, ram_mb, vcpus)
    return {"status": "RESCUE", "rescue_disk": str(rescue_disk)}


def unrescue_server(server_id: str, payload: dict) -> dict:
    """Restore VM from rescue — boot back from original root disk."""
    domain = payload["libvirt_domain_name"]
    tap = payload.get("tap_name") or network_manager.tap_name(server_id)
    mac_address = payload.get("mac_address", "")
    flavor = payload.get("flavor_snapshot") or {}
    ram_mb = int(payload.get("ram_mb") or flavor.get("ram_mb") or 512)
    vcpus = int(payload.get("vcpus") or flavor.get("vcpus") or 1)

    disk_path = payload.get("disk_path")
    if not disk_path:
        raise ValueError("unrescue payload missing disk_path")

    instance_dir = Path(payload.get("instance_dir") or INSTANCE_DIR / server_id)
    port = payload.get("port") or {}
    seed_iso = cloudinit.create_config_drive(
        instance_dir, server_id, payload.get("name", ""),
        payload.get("public_key_snapshot"), port, payload.get("user_data"),
    )
    expose_to_host_libvirt(seed_iso)
    libvirt_driver.rebuild(domain, disk_path, str(seed_iso), tap, mac_address, ram_mb, vcpus)

    rescue_disk = instance_dir / "disk-rescue.qcow2"
    if rescue_disk.exists():
        rescue_disk.unlink()

    return {"status": "ACTIVE"}


# ── Password injection ─────────────────────────────────────────────────────────

def set_server_password(payload: dict) -> dict:
    domain = payload["libvirt_domain_name"]
    password = payload.get("password", "")
    username = payload.get("username", "root")
    if not password:
        raise ValueError("password is required")
    libvirt_driver.set_password(domain, password, username)
    return {"status": "password set", "username": username}


# ── Console log ────────────────────────────────────────────────────────────────

def get_server_logs(payload: dict) -> dict:
    domain = payload["libvirt_domain_name"]
    lines = payload.get("lines", 50)
    output = libvirt_driver.get_console_log(domain, lines)
    return {
        "server": payload.get("name", ""),
        "domain": domain,
        "output": output,
        "lines": lines,
    }


# ── Delete ─────────────────────────────────────────────────────────────────────

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


# ── Port management ────────────────────────────────────────────────────────────

def attach_port(payload: dict) -> dict:
    domain = payload.get("libvirt_domain_name")
    if not domain:
        raise RuntimeError("server has no libvirt domain name")
    port = payload["port"]
    server_id = payload.get("id") or payload.get("server_id") or port.get("device_id")
    if not server_id:
        raise RuntimeError("server payload missing id for port tap")
    logical_port_name = port.get("logical_port_name") or port.get("name")
    mac_address = port["mac_address"]
    tap = network_manager.port_tap_name(server_id, port["id"])
    network_manager.create_tap(tap)
    network_manager.bind_tap_to_ovs(tap, logical_port_name, mac_address, server_id)
    try:
        libvirt_driver.attach_interface(domain, tap, mac_address)
    except Exception:
        network_manager.delete_tap(tap)
        raise
    return {"tap_name": tap, "logical_port_name": logical_port_name, "mac_address": mac_address}


def detach_port(payload: dict) -> dict:
    domain = payload.get("libvirt_domain_name")
    if not domain:
        raise RuntimeError("server has no libvirt domain name")
    port = payload["port"]
    server_id = payload.get("id") or payload.get("server_id") or port.get("device_id")
    tap = network_manager.port_tap_name(server_id, port["id"])
    try:
        libvirt_driver.detach_interface(domain, tap, port["mac_address"])
    finally:
        network_manager.delete_tap(tap)
    return {"tap_name": tap}
