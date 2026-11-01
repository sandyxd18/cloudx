import logging
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from . import network_manager
from .config import LIBVIRT_URI, VM_NAME_PREFIX
from .utils import short_id

log = logging.getLogger("compute-worker")

try:
    import libvirt
except ImportError:  # pragma: no cover - allows XML tests without host libvirt bindings
    libvirt = None


def domain_name(server_name: str, server_id: str) -> str:
    safe_name = "".join(ch if ch.isalnum() or ch in ("-", "_") else "-" for ch in server_name.lower())
    return f"{VM_NAME_PREFIX}-{safe_name}-{short_id(server_id, 8)}"


def _disk_path_type(path: str | Path) -> str:
    """Detect the storage type from a volume path.

    Returns:
      'block' — LVM block device  (/dev/...)
      'rbd'   — Ceph RBD network  (rbd:...)
      'file'  — legacy file disk  (anything else, e.g. *.qcow2)
    """
    s = str(path)
    if s.startswith("/dev/"):
        return "block"
    if s.startswith("rbd:"):
        return "rbd"
    return "file"


MON_V1_PORT = "6789"


def _parse_mon_hosts(raw: str) -> list[tuple[str, str]]:
    """Monitor endpoints from a ceph.conf mon_host value, one (host, port) per monitor.

    Handles the cephadm form '[v2:ip:3300/0,v1:ip:6789/0] [v2:...]', plain 'ip,ip' and 'host:port'. A monitor listed
    with both protocols is given by its v1 address, the port libvirt's <host> has always meant; a v2-only one by its v2 port.
    """
    endpoints = []
    for entry in re.findall(r"\[[^\]]*\]|[^\s,\[\]]+", raw):
        addrs = []
        for part in entry.strip("[]").split(","):
            part = part.strip().split("/")[0]
            version = None
            if part[:3] in ("v1:", "v2:"):
                version, part = part[:2], part[3:]
            host, sep, port = part.rpartition(":")
            if not sep:
                host, port = part, MON_V1_PORT
            addrs.append((version, host, port))
        if not addrs:
            continue
        v1 = [a for a in addrs if a[0] in (None, "v1")]
        _, host, port = (v1 or addrs)[0]
        endpoints.append((host, port))
    return endpoints


def _rbd_parts(rbd_path: str) -> tuple[str, str, list[tuple[str, str]]]:
    """Parse 'rbd:pool/image' → (pool, image, [(mon_host, port), ...])."""
    import configparser, os as _os
    without_prefix = rbd_path[len("rbd:"):]
    pool, _, image = without_prefix.partition("/")
    image = image.split("@")[0]   # strip snapshot suffix if present
    # Read mon_host from ceph.conf
    ceph_conf = _os.getenv("CEPH_CONF", "/etc/ceph/ceph.conf")
    cfg = configparser.ConfigParser()
    cfg.read(ceph_conf)
    mon_host = cfg.get("global", "mon_host", fallback="127.0.0.1")
    return pool, image, _parse_mon_hosts(mon_host)


def _rbd_disk_element(parent: ET.Element | None, disk_path: str | Path, target_dev: str, bus: str) -> ET.Element:
    """<disk type='network'> for an RBD image: every monitor as a <host>, and the cephx <auth> when CEPH_SECRET_UUID names the
    libvirt secret holding the key of CEPH_USER (default cloudx), see scripts/setup-ceph-libvirt-secret.sh."""
    import os as _os
    pool, image, hosts = _rbd_parts(str(disk_path))
    disk = ET.Element("disk", type="network", device="disk") if parent is None else ET.SubElement(parent, "disk", type="network", device="disk")
    ET.SubElement(disk, "driver", name="qemu", type="raw", cache="none")
    source = ET.SubElement(disk, "source", protocol="rbd", name=f"{pool}/{image}")
    for host, port in hosts:
        ET.SubElement(source, "host", name=host, port=port)
    secret_uuid = _os.getenv("CEPH_SECRET_UUID", "").strip()
    if secret_uuid:
        auth = ET.SubElement(disk, "auth", username=_os.getenv("CEPH_USER", "cloudx"))
        ET.SubElement(auth, "secret", type="ceph", uuid=secret_uuid)
    ET.SubElement(disk, "target", dev=target_dev, bus=bus)
    return disk


def _build_boot_disk_element(devices: ET.Element, disk_path: str | Path) -> None:
    """Append the correct root-disk XML element to <devices> based on path type."""
    path_type = _disk_path_type(disk_path)

    if path_type == "block":
        # LVM block device
        disk = ET.SubElement(devices, "disk", type="block", device="disk")
        ET.SubElement(disk, "driver", name="qemu", type="raw", cache="none", io="native")
        ET.SubElement(disk, "source", dev=str(disk_path))
        ET.SubElement(disk, "target", dev="sda", bus="scsi")
        ET.SubElement(disk, "address", type="drive", controller="0", bus="0", target="0", unit="0")

    elif path_type == "rbd":
        # Ceph RBD network disk
        disk = _rbd_disk_element(devices, disk_path, "sda", "scsi")
        ET.SubElement(disk, "address", type="drive", controller="0", bus="0", target="0", unit="0")

    else:
        # Legacy file disk (qcow2)
        disk = ET.SubElement(devices, "disk", type="file", device="disk")
        ET.SubElement(disk, "driver", name="qemu", type="qcow2", cache="none")
        ET.SubElement(disk, "source", file=str(disk_path))
        ET.SubElement(disk, "target", dev="sda", bus="scsi")
        ET.SubElement(disk, "address", type="drive", controller="0", bus="0", target="0", unit="0")


def domain_xml(
    name: str,
    memory_mb: int,
    vcpus: int,
    disk_path: str | Path,
    seed_iso: str | Path,
    tap_name: str,
    mac_address: str,
    extra_interfaces: list[tuple[str, str]] | None = None,
) -> str:
    domain = ET.Element("domain", type="kvm")
    ET.SubElement(domain, "name").text = name
    ET.SubElement(domain, "memory", unit="MiB").text = str(memory_mb)
    ET.SubElement(domain, "currentMemory", unit="MiB").text = str(memory_mb)
    ET.SubElement(domain, "vcpu", placement="static").text = str(vcpus)
    os_el = ET.SubElement(domain, "os")
    ET.SubElement(os_el, "type", arch="x86_64", machine="pc").text = "hvm"
    ET.SubElement(os_el, "boot", dev="hd")

    # ACPI and APIC are needed so the VM can shut down/reboot properly
    # and so the serial console works well
    features = ET.SubElement(domain, "features")
    ET.SubElement(features, "acpi")
    ET.SubElement(features, "apic")

    clock = ET.SubElement(domain, "clock", offset="utc")
    ET.SubElement(clock, "timer", name="rtc", tickpolicy="catchup")
    ET.SubElement(clock, "timer", name="pit", tickpolicy="delay")
    ET.SubElement(clock, "timer", name="hpet", present="no")
    ET.SubElement(clock, "timer", name="kvmclock", present="yes")

    ET.SubElement(domain, "on_poweroff").text = "destroy"
    ET.SubElement(domain, "on_reboot").text = "restart"
    ET.SubElement(domain, "on_crash").text = "restart"
    devices = ET.SubElement(domain, "devices")
    ET.SubElement(devices, "emulator").text = "/usr/bin/qemu-system-x86_64"
    ET.SubElement(devices, "controller", type="scsi", model="virtio-scsi")

    # Hardware watchdog to prevent silent guest kernel hangs during nested virt pause/resume
    ET.SubElement(devices, "watchdog", model="i6300esb", action="reset")

    # Root disk — auto-detect LVM block / Ceph RBD / legacy file
    _build_boot_disk_element(devices, disk_path)

    cdrom = ET.SubElement(devices, "disk", type="file", device="cdrom")
    ET.SubElement(cdrom, "driver", name="qemu", type="raw")
    ET.SubElement(cdrom, "source", file=str(seed_iso))
    ET.SubElement(cdrom, "target", dev="hda", bus="ide")
    ET.SubElement(cdrom, "readonly")

    for tap, mac in [(tap_name, mac_address), *(extra_interfaces or [])]:  # (tap, MAC) of each port, primary first
        iface = ET.SubElement(devices, "interface", type="ethernet")
        ET.SubElement(iface, "mac", address=mac)
        ET.SubElement(iface, "target", dev=tap, managed="no")
        ET.SubElement(iface, "model", type="virtio")

    # Serial console — target type="serial" port 0 so virsh console attaches to ttyS0
    instance_dir = Path(seed_iso).parent
    console_log_path = instance_dir / "console.log"

    console = ET.SubElement(devices, "console", type="pty")
    ET.SubElement(console, "target", type="serial", port="0")
    ET.SubElement(console, "log", file=str(console_log_path), append="on")

    # virtio-balloon for better memory management
    ET.SubElement(devices, "memballoon", model="virtio")

    ET.SubElement(devices, "graphics", type="vnc", port="-1", autoport="yes", listen="127.0.0.1")
    return ET.tostring(domain, encoding="unicode")




def connect():
    if libvirt is None:
        raise RuntimeError("libvirt-python is not installed")
    conn = libvirt.open(LIBVIRT_URI)
    if conn is None:
        raise RuntimeError(f"failed to connect to libvirt URI {LIBVIRT_URI}")
    return conn


# Map libvirt's VIR_DOMAIN_* state ints to the reduced set this driver reports.
# (state ints: 0=NOSTATE 1=RUNNING 2=BLOCKED 3=PAUSED 4=SHUTDOWN 5=SHUTOFF
#  6=CRASHED 7=PMSUSPENDED)
_LIBVIRT_STATE_MAP = {
    1: "RUNNING",
    2: "RUNNING",   # blocked-on-resource still counts as running for our purposes
    3: "PAUSED",
    4: "SHUTOFF",   # in the middle of shutting down
    5: "SHUTOFF",
    6: "SHUTOFF",   # crashed
    7: "PAUSED",    # suspended-to-RAM by the host
}


def _has_managed_save(domain) -> bool:
    try:
        return bool(domain.hasManagedSaveImage(0))
    except Exception:  # best-effort: if libvirt cannot say, the domain is reported shut off as before
        return False


def get_all_domain_states() -> dict[str, str]:
    """Snapshot every domain known to this hypervisor: {domain_name: state}.

    ``state`` is one of ``RUNNING`` / ``PAUSED`` / ``SUSPENDED`` / ``SHUTOFF``.
    A domain that is not running and has no managed save shows up as ``SHUTOFF``;
    one that was suspended to disk (``suspend_to_disk``) is shut off as far as
    libvirt is concerned, but holds a managed save and shows up as ``SUSPENDED``.
    Only a domain absent from this dict is truly "MISSING" from the hypervisor
    (undefined, e.g. after ``virsh undefine`` or a destroyed transient domain).

    Used by the power-state reconciliation loop; never raises — callers should
    treat an empty dict + logged warning as "hypervisor unreachable, skip sync".
    """
    if libvirt is None:
        raise RuntimeError("libvirt-python is not installed")
    conn = connect()
    try:
        states: dict[str, str] = {}
        for domain in conn.listAllDomains():
            state_id, _reason = domain.state()
            state = _LIBVIRT_STATE_MAP.get(state_id, "SHUTOFF")
            if state == "SHUTOFF" and _has_managed_save(domain):
                state = "SUSPENDED"
            states[domain.name()] = state
        return states
    finally:
        conn.close()


def get_domain_raw_counters(name: str, tap_name: str | None, disk_target: str = "sda") -> dict | None:
    """Read the current instantaneous hypervisor counters for one domain.

    Returns raw cumulative counters (not rates) — the caller is responsible for
    diffing two samples over the elapsed wall-clock time to get CPU %, bytes/sec,
    and IOPS. Returns None if the domain isn't running (no live stats to read).

    Fields:
      cpu_time_ns   — cumulative CPU time in nanoseconds (domain.info()[4])
      nr_vcpus      — number of vCPUs, needed to normalize CPU time into a 0-100% figure
      mem_total_kb  — max/allocated memory (KB)
      mem_used_kb   — actual guest memory in use (KB), from memoryStats() 'available'-'unused'
      net_rx_bytes / net_tx_bytes / net_rx_packets / net_tx_packets — cumulative, from interfaceStats
      disk_read_bytes / disk_write_bytes / disk_read_req / disk_write_req — cumulative, from blockStats
      timestamp     — time.monotonic() at sample time, for rate calculation
    """
    if libvirt is None:
        raise RuntimeError("libvirt-python is not installed")
    import time as _time
    conn, domain = lookup(name)
    try:
        if not domain.isActive():
            return None

        # virDomainGetInfo() returns (state, maxMem, memory, nrVirtCpu, cpuTime).
        _state, max_mem_kb, current_mem_kb, nr_vcpus, cpu_time_ns = domain.info()

        mem_used_kb = current_mem_kb
        try:
            stats = domain.memoryStats()
            # 'available' and 'unused' are only reported when the guest has the
            # virtio balloon driver + qemu-guest-agent; fall back to domain.info().
            if "available" in stats and "unused" in stats:
                mem_used_kb = stats["available"] - stats["unused"]
            elif "rss" in stats:
                mem_used_kb = stats["rss"]
        except Exception as exc:
            log.debug("memory stats unavailable: %s", exc)

        net_rx_bytes = net_tx_bytes = net_rx_packets = net_tx_packets = 0
        if tap_name:
            try:
                rx_bytes, rx_packets, _rx_errs, _rx_drop, tx_bytes, tx_packets, _tx_errs, _tx_drop = \
                    domain.interfaceStats(tap_name)
                net_rx_bytes, net_tx_bytes = rx_bytes, tx_bytes
                net_rx_packets, net_tx_packets = rx_packets, tx_packets
            except Exception as exc:
                log.debug("interface stats unavailable: %s", exc)

        disk_read_req = disk_read_bytes = disk_write_req = disk_write_bytes = 0
        try:
            read_req, read_bytes, write_req, write_bytes, _errs = domain.blockStats(disk_target)
            disk_read_req, disk_read_bytes = read_req, read_bytes
            disk_write_req, disk_write_bytes = write_req, write_bytes
        except Exception as exc:
            log.debug("block stats unavailable: %s", exc)

        return {
            "cpu_time_ns": cpu_time_ns,
            "nr_vcpus": nr_vcpus,
            "mem_total_kb": max_mem_kb,
            "mem_used_kb": mem_used_kb,
            "net_rx_bytes": net_rx_bytes,
            "net_tx_bytes": net_tx_bytes,
            "net_rx_packets": net_rx_packets,
            "net_tx_packets": net_tx_packets,
            "disk_read_bytes": disk_read_bytes,
            "disk_write_bytes": disk_write_bytes,
            "disk_read_req": disk_read_req,
            "disk_write_req": disk_write_req,
            "timestamp": _time.monotonic(),
        }
    finally:
        conn.close()


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


def _ensure_unmanaged_taps(domain) -> None:
    """Recreate the tap devices a host reboot removed; libvirt refuses to start a managed='no' interface without one."""
    for target in ET.fromstring(domain.XMLDesc(0)).findall("./devices/interface/target"):
        if target.get("managed") == "no" and target.get("dev"):
            network_manager.ensure_tap(target.get("dev"))


def start(name: str) -> None:
    conn, domain = lookup(name)
    try:
        if not domain.isActive():
            _ensure_unmanaged_taps(domain)
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


_BY_PATH = re.compile(r"/dev/disk/by-path/ip-(\S+?)-iscsi-(\S+)-lun-\d+\Z")

def iscsi_sessions_from_xml(xml: str) -> list[tuple[str, str]]:
    """(portal, target iqn) of every cross-node iSCSI disk of a domain (they are plain by-path block disks)."""
    found = []
    for src in ET.fromstring(xml).findall("./devices/disk/source"):
        m = _BY_PATH.match(src.attrib.get("dev", ""))
        if m:
            found.append((m.group(1), m.group(2)))
    return found

def iscsi_sessions(name: str) -> list[tuple[str, str]]:
    conn = connect()
    try:
        return iscsi_sessions_from_xml(conn.lookupByName(name).XMLDesc(0))
    finally:
        conn.close()

def interface_taps_from_xml(xml: str) -> list[str]:
    """Tap devices of a domain: the target of each ethernet interface (CloudX plugs every port of a server that way)."""
    return [t.attrib["dev"] for t in ET.fromstring(xml).findall("./devices/interface[@type='ethernet']/target") if t.attrib.get("dev")]

def interface_taps(name: str) -> list[str]:
    conn = connect()
    try:
        return interface_taps_from_xml(conn.lookupByName(name).XMLDesc(0))
    finally:
        conn.close()

def delete(name: str) -> None:
    conn = connect()
    try:
        try:
            domain = conn.lookupByName(name)
        except Exception as exc:
            log.info("domain %s not found, nothing to delete: %s", name, exc)
            return
        if domain.isActive():
            domain.destroy()
        try:
            flags = getattr(libvirt, "VIR_DOMAIN_UNDEFINE_MANAGED_SAVE", 1) | getattr(libvirt, "VIR_DOMAIN_UNDEFINE_NVRAM", 4)
            domain.undefineFlags(flags)
        except Exception as exc:
            log.info("undefineFlags unsupported, using plain undefine: %s", exc)
            domain.undefine()
    finally:
        conn.close()


def disk_device_xml(path: str | Path, target_dev: str = "sdb", disk_format: str = "auto", bus: str = "scsi") -> str:
    """Build libvirt disk XML for a data volume.

    Detects path type automatically:
      /dev/... → block device (LVM)       — type=raw, io=native
      rbd:...  → Ceph RBD network disk    — type=raw, protocol=rbd
      other    → file disk (legacy qcow2) — type=qcow2
    """
    path_type = _disk_path_type(path)

    if path_type == "block":
        disk = ET.Element("disk", type="block", device="disk")
        ET.SubElement(disk, "driver", name="qemu", type="raw", cache="none", io="native")
        ET.SubElement(disk, "source", dev=str(path))
        ET.SubElement(disk, "target", dev=target_dev, bus=bus)
        if bus == "scsi" and target_dev.startswith("sd") and len(target_dev) == 3:
            unit = str(ord(target_dev[-1]) - ord("a"))
            ET.SubElement(disk, "address", type="drive", controller="0", bus="0", target="0", unit=unit)

    elif path_type == "rbd":
        disk = _rbd_disk_element(None, path, target_dev, bus)

    else:
        # Legacy file
        fmt = disk_format if disk_format != "auto" else "qcow2"
        disk = ET.Element("disk", type="file", device="disk")
        ET.SubElement(disk, "driver", name="qemu", type=fmt, cache="none")
        ET.SubElement(disk, "source", file=str(path))
        ET.SubElement(disk, "target", dev=target_dev, bus=bus)
        if bus == "scsi" and target_dev.startswith("sd") and len(target_dev) == 3:
            unit = str(ord(target_dev[-1]) - ord("a"))
            ET.SubElement(disk, "address", type="drive", controller="0", bus="0", target="0", unit=unit)

    return ET.tostring(disk, encoding="unicode")


def attach_disk(name: str, path: str | Path, target_dev: str = "sdb", disk_format: str = "auto", bus: str = "scsi") -> None:
    conn, domain = lookup(name)
    try:
        xml = disk_device_xml(path, target_dev, disk_format, bus)
        flags = getattr(libvirt, "VIR_DOMAIN_AFFECT_CONFIG", 2)
        if domain.isActive():
            flags |= getattr(libvirt, "VIR_DOMAIN_AFFECT_LIVE", 1)
        domain.attachDeviceFlags(xml, flags)
    finally:
        conn.close()


def detach_disk(name: str, path: str | Path, target_dev: str = "sdb", disk_format: str = "auto", bus: str = "scsi") -> None:
    conn, domain = lookup(name)
    try:
        xml = disk_device_xml(path, target_dev, disk_format, bus)
        flags = getattr(libvirt, "VIR_DOMAIN_AFFECT_CONFIG", 2)
        if domain.isActive():
            flags |= getattr(libvirt, "VIR_DOMAIN_AFFECT_LIVE", 1)
        domain.detachDeviceFlags(xml, flags)
    finally:
        conn.close()



def interface_xml(tap_name: str, mac_address: str) -> str:
    iface = ET.Element("interface", type="ethernet")
    ET.SubElement(iface, "mac", address=mac_address)
    ET.SubElement(iface, "target", dev=tap_name, managed="no")
    ET.SubElement(iface, "model", type="virtio")
    return ET.tostring(iface, encoding="unicode")


def attach_interface(name: str, tap_name: str, mac_address: str) -> None:
    conn, domain = lookup(name)
    try:
        flags = getattr(libvirt, "VIR_DOMAIN_AFFECT_CONFIG", 2)
        if domain.isActive():
            flags |= getattr(libvirt, "VIR_DOMAIN_AFFECT_LIVE", 1)
        domain.attachDeviceFlags(interface_xml(tap_name, mac_address), flags)
    finally:
        conn.close()


def detach_interface(name: str, tap_name: str, mac_address: str) -> None:
    conn, domain = lookup(name)
    try:
        flags = getattr(libvirt, "VIR_DOMAIN_AFFECT_CONFIG", 2)
        if domain.isActive():
            flags |= getattr(libvirt, "VIR_DOMAIN_AFFECT_LIVE", 1)
        domain.detachDeviceFlags(interface_xml(tap_name, mac_address), flags)
    finally:
        conn.close()

def vnc_info(name: str) -> dict:
    conn, domain = lookup(name)
    try:
        root = ET.fromstring(domain.XMLDesc(0))
        graphics = root.find("./devices/graphics[@type='vnc']")
        if graphics is None:
            return {"status": "not-configured"}
        port = graphics.attrib.get("port")
        listen = graphics.attrib.get("listen", "127.0.0.1")
        return {"status": "available" if port and port != "-1" else "auto-assigned", "listen": listen, "port": int(port) if port and port != "-1" else None}
    finally:
        conn.close()


def console_hint(name: str) -> str:
    return f"virsh -c {LIBVIRT_URI} console {name}"


# ── Power state management ─────────────────────────────────────────────────────

def pause(name: str) -> None:
    """Suspend (pause) the VM at the hypervisor level (PAUSED state)."""
    conn, domain = lookup(name)
    try:
        if domain.isActive():
            domain.suspend()
    finally:
        conn.close()


def resume(name: str) -> None:
    """Resume a paused (suspended at hypervisor level) VM."""
    conn, domain = lookup(name)
    try:
        state, _ = domain.state()
        # VIR_DOMAIN_PAUSED = 3
        if state == 3:
            domain.resume()
    finally:
        conn.close()


def suspend_to_disk(name: str) -> None:
    """Save VM state to disk (managed save), similar to OS suspend-to-disk."""
    conn, domain = lookup(name)
    try:
        if domain.isActive():
            domain.managedSave(0)
    finally:
        conn.close()


def resume_from_disk(name: str) -> None:
    """Restore a managed-saved VM (starts from saved state)."""
    conn, domain = lookup(name)
    try:
        if not domain.isActive():
            _ensure_unmanaged_taps(domain)
            domain.create()  # restores from managed save if it exists
    finally:
        conn.close()


# ── Live resize ────────────────────────────────────────────────────────────────

def resize(name: str, new_vcpus: int | None, new_ram_mb: int | None) -> None:
    """Hot-plug CPU and/or memory.  Both parameters are optional; pass None to skip."""
    conn, domain = lookup(name)
    try:
        live = getattr(libvirt, "VIR_DOMAIN_AFFECT_LIVE", 1)
        cfg  = getattr(libvirt, "VIR_DOMAIN_AFFECT_CONFIG", 2)
        flags = live | cfg

        if new_vcpus is not None:
            # setVcpusFlags can fail if the guest doesn't support VCPU hotplug;
            # we fall back to config-only so the change takes effect on next boot.
            try:
                domain.setVcpusFlags(new_vcpus, flags)
            except Exception as exc:
                log.info("live vCPU change not possible, applied to the persistent config only: %s", exc)
                domain.setVcpusFlags(new_vcpus, cfg)

        if new_ram_mb is not None:
            ram_kb = new_ram_mb * 1024
            try:
                domain.setMemoryFlags(ram_kb, flags)
            except Exception as exc:
                log.info("live memory change not possible, applied to the persistent config only: %s", exc)
                domain.setMemoryFlags(ram_kb, cfg)
    finally:
        conn.close()


# ── Console log ────────────────────────────────────────────────────────────────

def get_console_log(name: str, lines: int = 50) -> str:
    """Retrieve recent serial console output via libvirt screenshot / log file.

    We configure the VM with <log file="...console.log" append="on"/> on its
    PTY console, so we can just read that log file from the instance directory.
    """
    from shared.proc import run

    from .config import LIBVIRT_URI
    try:
        # Read XML to find the log file path
        result = run(["virsh", "-c", LIBVIRT_URI, "dumpxml", name], check=False, timeout=10)
        if result.returncode != 0:
            return ""
        root = ET.fromstring(result.stdout)
        console_el = root.find("./devices/console[@type='pty']")
        if console_el is not None:
            log_el = console_el.find("log")
            if log_el is not None:
                log_file = log_el.attrib.get("file", "")
                if log_file and Path(log_file).exists():
                    tail = run(["tail", "-n", str(lines), log_file], check=False, timeout=5)
                    return tail.stdout
    except Exception as exc:
        return f"[Error fetching logs: {exc}]"
    return ""


# ── Password injection via QEMU guest agent ────────────────────────────────────

def set_password(name: str, password: str, username: str = "root") -> None:
    """Inject a password via QEMU Guest Agent (requires qemu-guest-agent in VM)."""
    import json as _json
    conn, domain = lookup(name)
    try:
        cmd = _json.dumps({
            "execute": "guest-set-user-password",
            "arguments": {
                "username": username,
                "password": password,
                "crypted": False,
            },
        })
        result = domain.qemuAgentCommand(cmd, 30, 0)
        if result and "error" in _json.loads(result):
            raise RuntimeError(f"guest agent error: {_json.loads(result)['error']}")
    finally:
        conn.close()


# ── Rebuild (re-provision root disk) ──────────────────────────────────────────

def rebuild(name: str, new_disk_path: str, seed_iso: str, tap_name: str,
            mac_address: str, ram_mb: int, vcpus: int) -> None:
    """Destroy current domain, swap root disk, redefine and start."""
    conn = connect()
    try:
        try:
            domain = conn.lookupByName(name)
            if domain.isActive():
                domain.destroy()
            try:
                flags = getattr(libvirt, "VIR_DOMAIN_UNDEFINE_MANAGED_SAVE", 1) | getattr(libvirt, "VIR_DOMAIN_UNDEFINE_NVRAM", 4)
                domain.undefineFlags(flags)
            except Exception as exc:
                log.info("undefineFlags unsupported, using plain undefine: %s", exc)
                domain.undefine()
        except Exception as exc:
            log.warning("could not remove the old domain %s before rebuild: %s", name, exc)
        xml = domain_xml(name, ram_mb, vcpus, new_disk_path, seed_iso, tap_name, mac_address)
        new_domain = conn.defineXML(xml)
        new_domain.create()
    finally:
        conn.close()

