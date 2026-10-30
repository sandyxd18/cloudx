"""
Ceph RBD volume driver for cloudx block storage.

Requires:
  - STORAGE_CEPH_RBD_DRIVER=true in env
  - Ceph cluster reachable from the host
  - rbd CLI available in container (install ceph-common)
  - CEPH_POOL env var set (e.g. "volumes")
  - CEPH_CONF pointing to ceph.conf (default: /etc/ceph/ceph.conf)
  - CEPH_KEYRING pointing to keyring (optional, uses default if absent)
  - CEPH_USER cephx user, e.g. "cloudx" (optional; rbd and qemu-img both use it)

Volumes are accessed by VMs via libvirt XML network disk:
  <disk type='network' device='disk'>
    <driver name='qemu' type='raw'/>
    <source protocol='rbd' name='{pool}/{image_name}'>
      <host name='{mon_host}' port='6789'/>
    </source>
    <target dev='vdb' bus='virtio'/>
  </disk>
"""

import json
import logging
import os

from shared.proc import output, run

log = logging.getLogger(__name__)

CEPH_POOL    = os.getenv("CEPH_POOL", "volumes")
CEPH_CONF    = os.getenv("CEPH_CONF", "/etc/ceph/ceph.conf")
CEPH_KEYRING = os.getenv("CEPH_KEYRING", "")
CEPH_USER    = os.getenv("CEPH_USER", "")  # cephx user without the "client." prefix; empty = the client library default (admin)


def _rbd(*args: str, timeout: float | None = 300) -> str:
    cmd = ["rbd", "--conf", CEPH_CONF]
    if CEPH_USER:
        cmd += ["--id", CEPH_USER]
    if CEPH_KEYRING:
        cmd += ["--keyring", CEPH_KEYRING]
    cmd += list(args)
    return output(cmd, timeout=timeout)  # CommandError (a RuntimeError) on failure


def pool() -> str:
    if not CEPH_POOL:
        raise RuntimeError("CEPH_POOL is not configured. Set it in .env")
    return CEPH_POOL


def create(image_name: str, size_gb: int) -> str:
    """Create an RBD image. Returns rbd://pool/image_name path identifier."""
    p = pool()
    _rbd("create", f"{p}/{image_name}", "--size", f"{size_gb * 1024}M",
         "--image-feature", "layering")
    return f"rbd:{p}/{image_name}"


def create_from_source(image_name: str, source_path: str, size_gb: int) -> str:
    """Convert an image file (qcow2, raw, ...) into a new raw RBD image of size_gb.

    Used when creating a boot volume from an image. Returns the rbd path identifier. A failed conversion is an
    error: importing the file as-is would put qcow2 bytes in an image the VM reads as raw.
    """
    p = pool()
    target = f"rbd:{p}/{image_name}"
    if CEPH_USER:
        target += f":id={CEPH_USER}"  # CEPH_ARGS (--id) is not read by qemu-img
    target += f":conf={CEPH_CONF}"
    if CEPH_KEYRING:
        target += f":keyring={CEPH_KEYRING}"
    run(["qemu-img", "convert", "-O", "raw", source_path, target], timeout=None)  # needs qemu-utils on this node

    # Resize if needed (source might be smaller than requested size)
    _rbd("resize", f"{p}/{image_name}", "--size", f"{size_gb * 1024}M", "--allow-shrink")
    return f"rbd:{p}/{image_name}"


def delete(image_name: str) -> None:
    """Remove an RBD image."""
    _rbd("rm", f"{pool()}/{image_name}")


def extend(image_name: str, new_size_gb: int) -> None:
    """Resize an RBD image to new_size_gb."""
    _rbd("resize", f"{pool()}/{image_name}", "--size", f"{new_size_gb * 1024}M", "--allow-shrink")


def snapshot(image_name: str, snapshot_name: str, _size_gb: int = 0) -> str:
    """Create an RBD snapshot. Returns rbd:pool/image@snap path.

    _size_gb is ignored for Ceph (snapshots are free in Ceph).
    """
    p = pool()
    _rbd("snap", "create", f"{p}/{image_name}@{snapshot_name}")
    return f"rbd:{p}/{image_name}@{snapshot_name}"


def delete_snapshot(path: str) -> None:
    """Remove the RBD snapshot a snapshot() call created; path is the rbd:pool/image@snap it returned."""
    _rbd("snap", "rm", path.removeprefix("rbd:"))


def clone(source_image: str, source_snap: str, dest_image: str) -> str:
    """Clone an RBD image from a protected snapshot."""
    p = pool()
    _rbd("snap", "protect", f"{p}/{source_image}@{source_snap}")
    _rbd("clone", f"{p}/{source_image}@{source_snap}", f"{p}/{dest_image}")
    return f"rbd:{p}/{dest_image}"


def _info_exists(spec: str) -> bool:
    try:
        _rbd("info", spec)
        return True
    except RuntimeError as exc:
        if "No such file or directory" in str(exc):   # rbd: error opening image ...: (2) No such file or directory
            return False
        raise


def exists(image_name: str) -> bool:
    return _info_exists(f"{pool()}/{image_name}")


def snapshot_exists(path: str) -> bool:
    """Whether the RBD snapshot a snapshot() call created is still there; path is the rbd:pool/image@snap it returned."""
    return _info_exists(path.removeprefix("rbd:"))


def list_images() -> list[str]:
    try:
        out = _rbd("ls", pool(), "--format", "json")
        return json.loads(out)
    except Exception as exc:
        log.warning("rbd ls failed: %s", exc)
        return []


def get_mon_host() -> str:
    """Extract monitor host from ceph.conf for libvirt XML generation."""
    import configparser
    cfg = configparser.ConfigParser()
    cfg.read(CEPH_CONF)
    return cfg.get("global", "mon_host", fallback="127.0.0.1")


def libvirt_disk_xml(image_name: str, dev: str = "sda", bus: str = "scsi") -> str:
    """
    Generate libvirt XML for attaching a Ceph RBD volume to a VM.
    Used by compute_worker when creating/attaching volumes with backend=ceph.
    """
    p    = pool()
    host = get_mon_host()
    disk = f"""<disk type='network' device='disk'>
  <driver name='qemu' type='raw' cache='none'/>
  <source protocol='rbd' name='{p}/{image_name}'>
    <host name='{host}' port='6789'/>
  </source>
  <target dev='{dev}' bus='{bus}'/>
</disk>"""
    return disk


# ── QoS Enforcement ───────────────────────────────────────────────────────────

def set_qos(image_name: str, max_iops: int | None = None, max_throughput_mbps: int | None = None) -> None:
    """
    Apply QoS limits to a Ceph RBD image using rbd config image set.

    Args:
        image_name: the RBD image name (without pool prefix).
        max_iops: maximum IOPS limit (both read + write combined).
        max_throughput_mbps: maximum throughput in MB/s (converted to BPS for Ceph).

    These map to Ceph's per-image config keys:
        rbd_qos_iops_limit   — combined read+write IOPS ceiling
        rbd_qos_bps_limit    — combined read+write bytes-per-second ceiling
    """
    image_ref = f"{pool()}/{image_name}"
    if max_iops is not None and max_iops > 0:
        _rbd("config", "image", "set", image_ref, "rbd_qos_iops_limit", str(max_iops))
        log.info("Ceph QoS: set rbd_qos_iops_limit=%d on %s", max_iops, image_ref)
    if max_throughput_mbps is not None and max_throughput_mbps > 0:
        # Ceph uses bytes-per-second
        bps = max_throughput_mbps * 1024 * 1024
        _rbd("config", "image", "set", image_ref, "rbd_qos_bps_limit", str(bps))
        log.info("Ceph QoS: set rbd_qos_bps_limit=%d on %s", bps, image_ref)


def clear_qos(image_name: str) -> None:
    """Remove any QoS limits from a Ceph RBD image."""
    image_ref = f"{pool()}/{image_name}"
    for key in ("rbd_qos_iops_limit", "rbd_qos_bps_limit"):
        try:
            _rbd("config", "image", "remove", image_ref, key)
        except RuntimeError:
            pass  # key may not exist; ignore
    log.info("Ceph QoS: cleared QoS config on %s", image_ref)
