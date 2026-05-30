import xml.etree.ElementTree as ET
from pathlib import Path

from .config import LIBVIRT_URI, VM_NAME_PREFIX
from .utils import short_id

try:
    import libvirt
except ImportError:  # pragma: no cover - allows XML tests without host libvirt bindings
    libvirt = None


def domain_name(server_name: str, server_id: str) -> str:
    safe_name = "".join(ch if ch.isalnum() or ch in ("-", "_") else "-" for ch in server_name.lower())
    return f"{VM_NAME_PREFIX}-{safe_name}-{short_id(server_id, 8)}"


def domain_xml(
    name: str,
    memory_mb: int,
    vcpus: int,
    disk_path: str | Path,
    seed_iso: str | Path,
    tap_name: str,
    mac_address: str,
) -> str:
    domain = ET.Element("domain", type="kvm")
    ET.SubElement(domain, "name").text = name
    ET.SubElement(domain, "memory", unit="MiB").text = str(memory_mb)
    ET.SubElement(domain, "currentMemory", unit="MiB").text = str(memory_mb)
    ET.SubElement(domain, "vcpu", placement="static").text = str(vcpus)
    os_el = ET.SubElement(domain, "os")
    ET.SubElement(os_el, "type", arch="x86_64", machine="pc").text = "hvm"
    ET.SubElement(os_el, "boot", dev="hd")
    ET.SubElement(domain, "features")
    ET.SubElement(domain, "clock", offset="utc")
    ET.SubElement(domain, "on_poweroff").text = "destroy"
    ET.SubElement(domain, "on_reboot").text = "restart"
    ET.SubElement(domain, "on_crash").text = "destroy"
    devices = ET.SubElement(domain, "devices")
    ET.SubElement(devices, "emulator").text = "/usr/bin/qemu-system-x86_64"
    ET.SubElement(devices, "controller", type="scsi", model="virtio-scsi")

    disk = ET.SubElement(devices, "disk", type="file", device="disk")
    ET.SubElement(disk, "driver", name="qemu", type="qcow2", cache="none")
    ET.SubElement(disk, "source", file=str(disk_path))
    ET.SubElement(disk, "target", dev="sda", bus="scsi")
    ET.SubElement(disk, "address", type="drive", controller="0", bus="0", target="0", unit="0")

    cdrom = ET.SubElement(devices, "disk", type="file", device="cdrom")
    ET.SubElement(cdrom, "driver", name="qemu", type="raw")
    ET.SubElement(cdrom, "source", file=str(seed_iso))
    ET.SubElement(cdrom, "target", dev="hda", bus="ide")
    ET.SubElement(cdrom, "readonly")

    iface = ET.SubElement(devices, "interface", type="ethernet")
    ET.SubElement(iface, "mac", address=mac_address)
    ET.SubElement(iface, "target", dev=tap_name, managed="no")
    ET.SubElement(iface, "model", type="virtio")

    ET.SubElement(devices, "serial", type="pty")
    console = ET.SubElement(devices, "console", type="pty")
    ET.SubElement(console, "target", type="serial", port="0")
    ET.SubElement(devices, "graphics", type="vnc", port="-1", autoport="yes", listen="0.0.0.0")
    return ET.tostring(domain, encoding="unicode")


def connect():
    if libvirt is None:
        raise RuntimeError("libvirt-python is not installed")
    conn = libvirt.open(LIBVIRT_URI)
    if conn is None:
        raise RuntimeError(f"failed to connect to libvirt URI {LIBVIRT_URI}")
    return conn


def define_and_start(xml: str) -> None:
    conn = connect()
    try:
        domain = conn.defineXML(xml)
        if domain is None:
            raise RuntimeError("libvirt defineXML returned no domain")
        domain.create()
    finally:
        conn.close()


def lookup(name: str):
    conn = connect()
    try:
        return conn, conn.lookupByName(name)
    except Exception:
        conn.close()
        raise


def start(name: str) -> None:
    conn, domain = lookup(name)
    try:
        if not domain.isActive():
            domain.create()
    finally:
        conn.close()


def stop(name: str) -> None:
    conn, domain = lookup(name)
    try:
        if domain.isActive():
            domain.shutdown()
    finally:
        conn.close()


def reboot(name: str) -> None:
    conn, domain = lookup(name)
    try:
        domain.reboot()
    finally:
        conn.close()


def delete(name: str) -> None:
    conn = connect()
    try:
        try:
            domain = conn.lookupByName(name)
        except Exception:
            return
        if domain.isActive():
            domain.destroy()
        try:
            flags = getattr(libvirt, "VIR_DOMAIN_UNDEFINE_MANAGED_SAVE", 1) | getattr(libvirt, "VIR_DOMAIN_UNDEFINE_NVRAM", 4)
            domain.undefineFlags(flags)
        except Exception:
            domain.undefine()
    finally:
        conn.close()


def disk_device_xml(path: str | Path, target_dev: str = "sdb", disk_format: str = "qcow2", bus: str = "scsi") -> str:
    disk = ET.Element("disk", type="file", device="disk")
    ET.SubElement(disk, "driver", name="qemu", type=disk_format, cache="none")
    ET.SubElement(disk, "source", file=str(path))
    ET.SubElement(disk, "target", dev=target_dev, bus=bus)
    if bus == "scsi" and target_dev.startswith("sd") and len(target_dev) == 3:
        unit = str(ord(target_dev[-1]) - ord("a"))
        ET.SubElement(disk, "address", type="drive", controller="0", bus="0", target="0", unit=unit)
    return ET.tostring(disk, encoding="unicode")


def attach_disk(name: str, path: str | Path, target_dev: str = "sdb", disk_format: str = "qcow2", bus: str = "scsi") -> None:
    conn, domain = lookup(name)
    try:
        xml = disk_device_xml(path, target_dev, disk_format, bus)
        flags = getattr(libvirt, "VIR_DOMAIN_AFFECT_CONFIG", 2)
        if domain.isActive():
            flags |= getattr(libvirt, "VIR_DOMAIN_AFFECT_LIVE", 1)
        domain.attachDeviceFlags(xml, flags)
    finally:
        conn.close()


def detach_disk(name: str, path: str | Path, target_dev: str = "sdb", disk_format: str = "qcow2", bus: str = "scsi") -> None:
    conn, domain = lookup(name)
    try:
        xml = disk_device_xml(path, target_dev, disk_format, bus)
        flags = getattr(libvirt, "VIR_DOMAIN_AFFECT_CONFIG", 2)
        if domain.isActive():
            flags |= getattr(libvirt, "VIR_DOMAIN_AFFECT_LIVE", 1)
        domain.detachDeviceFlags(xml, flags)
    finally:
        conn.close()


def console_hint(name: str) -> str:
    return f"virsh -c {LIBVIRT_URI} console {name}"
