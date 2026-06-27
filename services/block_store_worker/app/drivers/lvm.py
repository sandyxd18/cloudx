"""
LVM volume driver for cloudx block storage.

Requires:
  - STORAGE_LVM_DRIVER=true in env
  - LVM_PV_NAME: comma-separated physical volumes (e.g. /dev/sdb,/dev/sdc)
  - LVM_VG_NAME: name of the volume group to create/use (e.g. vg-cloudx)
  - block-store container running with --privileged
  - lvcreate, lvremove, lvextend, vgcreate, pvcreate available in container

All volumes appear as /dev/{VG}/{LV} on the host (and in VM via libvirt block disk).
"""

import logging
import os
import subprocess

log = logging.getLogger(__name__)

LVM_VG_NAME = os.getenv("LVM_VG_NAME", "")
LVM_PV_NAME = os.getenv("LVM_PV_NAME", "")   # comma-separated, e.g. "/dev/sdb,/dev/sdc"

# LVM global config overrides for container environments:
# - udev_sync=0: don't wait for udev to process events (udev socket may not be available)
# - udev_rules=0: don't install udev rules for devices
# These prevent 'device not cleared' errors when running inside Docker.
LVM_CONTAINER_CFG = [
    "--config",
    "global { udev_sync=0 udev_rules=0 } activation { udev_sync=0 udev_rules=0 }",
]


def _run(cmd: list[str], check: bool = True) -> str:
    """Run an LVM command, raise RuntimeError on failure."""
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(f"LVM command failed: {' '.join(cmd)}\nstdout: {result.stdout}\nstderr: {result.stderr}")
    return result.stdout.strip()


def vg_name() -> str:
    if not LVM_VG_NAME:
        raise RuntimeError("LVM_VG_NAME is not configured. Set it in .env")
    return LVM_VG_NAME


def _vg_exists(vg: str) -> bool:
    result = subprocess.run(["vgdisplay", vg], capture_output=True)
    return result.returncode == 0


def _pv_exists(pv: str) -> bool:
    result = subprocess.run(["pvdisplay", pv], capture_output=True)
    return result.returncode == 0


def ensure_vg() -> None:
    """
    Ensure the VG exists. If not, create it from LVM_PV_NAME.

    Steps:
      1. Parse LVM_PV_NAME (comma-separated list of physical volumes)
      2. For each PV, run pvcreate if it is not already a PV
      3. Run vgcreate if the VG does not exist yet

    This function is idempotent — safe to call on every startup.
    """
    vg = vg_name()

    if not LVM_PV_NAME:
        raise RuntimeError(
            f"LVM_PV_NAME is not configured. Cannot auto-create VG '{vg}'. "
            "Set LVM_PV_NAME to a comma-separated list of block devices (e.g. /dev/sdb)."
        )

    pvs = [p.strip() for p in LVM_PV_NAME.split(",") if p.strip()]
    if not pvs:
        raise RuntimeError("LVM_PV_NAME is empty after parsing. Provide at least one block device.")

    if _vg_exists(vg):
        log.info("LVM: VG '%s' already exists — skipping creation", vg)
        return

    log.info("LVM: VG '%s' not found — creating from PVs: %s", vg, pvs)

    # pvcreate for each PV that is not already initialized
    for pv in pvs:
        if _pv_exists(pv):
            log.info("LVM: PV '%s' already initialized — skipping pvcreate", pv)
        else:
            log.info("LVM: pvcreate %s", pv)
            _run(["pvcreate", "-y", pv])

    # vgcreate
    log.info("LVM: vgcreate %s %s", vg, " ".join(pvs))
    _run(["vgcreate", vg] + pvs)
    log.info("LVM: VG '%s' created successfully", vg)


def _udev_settle() -> None:
    """Ask udev to process pending events. No-op if udevadm is not available."""
    try:
        subprocess.run(["udevadm", "settle", "--timeout=10"], capture_output=True)
    except FileNotFoundError:
        pass  # udevadm not in container — safe to skip when LVM udev_sync=0


def _cleanup_stale_lv(volume_name: str, vg: str) -> None:
    """Remove stale device-mapper entry and LV if they exist but were not cleanly removed.

    The 'device not cleared' error happens when a previous lvcreate left a stale
    device-mapper mapping. This cleans it up before creating a new LV.
    """
    dev_path = f"/dev/{vg}/{volume_name}"
    dm_name = f"{vg}-{volume_name}"

    # Check if this LV already exists
    lv_exists = subprocess.run(
        ["lvs", "--noheadings", "-o", "lv_name", f"{vg}/{volume_name}"],
        capture_output=True,
    ).returncode == 0

    if lv_exists:
        log.info("LVM: removing existing LV %s before re-creating", dev_path)
        subprocess.run(["lvremove", "-f", dev_path] + LVM_CONTAINER_CFG, capture_output=True)
        _udev_settle()

    # Also forcibly remove stale dm entry (no-op if not present)
    r = subprocess.run(["dmsetup", "info", dm_name], capture_output=True)
    if r.returncode == 0:
        log.info("LVM: removing stale dm entry %s", dm_name)
        subprocess.run(["dmsetup", "remove", "--force", "--retry", dm_name], capture_output=True)
        _udev_settle()




def create(volume_name: str, size_gb: int) -> str:
    """Create an LV and return its device path (/dev/VG/LV)."""
    vg = vg_name()
    _cleanup_stale_lv(volume_name, vg)
    _run(["lvcreate", "-y", "-W", "y", "-Z", "y", "-L", f"{size_gb}G", "-n", volume_name, vg] + LVM_CONTAINER_CFG)
    return f"/dev/{vg}/{volume_name}"


def create_from_source(volume_name: str, source_path: str, size_gb: int) -> str:
    """Create an LV and fill it with data from source_path (raw image or block device).
    
    Used when creating a volume from an image (boot volume).
    Steps:
      1. Cleanup stale device-mapper entry (prevents 'device not cleared' error)
      2. lvcreate with requested size (udev sync disabled for container compat)
      3. qemu-img convert source → /dev/VG/LV (dd fallback)
      4. Resize filesystem if needed (best-effort)
    """
    vg = vg_name()
    dev = f"/dev/{vg}/{volume_name}"

    # Cleanup stale entries before creating
    _cleanup_stale_lv(volume_name, vg)

    # Create the LV with udev sync disabled (required inside Docker containers)
    _run(["lvcreate", "-y", "-W", "y", "-Z", "y", "-L", f"{size_gb}G", "-n", volume_name, vg] + LVM_CONTAINER_CFG)

    # Give the kernel a moment to register the new device node
    _udev_settle()

    # Write image to block device using qemu-img convert for format compatibility
    result = subprocess.run(
        ["qemu-img", "convert", "-O", "raw", source_path, dev],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        # Fallback to dd if qemu-img unavailable or fails
        log.warning("qemu-img convert failed, falling back to dd: %s", result.stderr)
        _run(["dd", f"if={source_path}", f"of={dev}", "bs=4M", "status=none"])

    # Best-effort resize (growfs/resize2fs/xfs_growfs)
    _try_resize_fs(dev)

    return dev


def _try_resize_fs(dev: str) -> None:
    """Try to grow the filesystem on the device (best-effort, no error if fails)."""
    for cmd in [
        ["growpart", dev, "1"],
        ["resize2fs", dev],
        ["xfs_growfs", dev],
    ]:
        try:
            subprocess.run(cmd, capture_output=True, timeout=30)
        except Exception:
            pass


def delete(volume_name: str) -> None:
    """Remove an LV."""
    vg = vg_name()
    _run(["lvremove", "-f", f"{vg}/{volume_name}"] + LVM_CONTAINER_CFG)


def extend(volume_name: str, new_size_gb: int) -> None:
    """Resize an LV to new_size_gb (must be larger than current)."""
    vg = vg_name()
    _run(["lvextend", "-L", f"{new_size_gb}G", f"{vg}/{volume_name}"] + LVM_CONTAINER_CFG)
    # Try to extend the filesystem in-place
    dev = f"/dev/{vg}/{volume_name}"
    _try_resize_fs(dev)


def device_path(volume_name: str) -> str:
    return f"/dev/{vg_name()}/{volume_name}"


def exists(volume_name: str) -> bool:
    vg = vg_name()
    result = subprocess.run(["lvdisplay", f"{vg}/{volume_name}"], capture_output=True)
    return result.returncode == 0


def list_volumes() -> list[str]:
    """Return a list of LV names in the VG."""
    vg = vg_name()
    out = _run(["lvs", "--noheadings", "-o", "lv_name", vg], check=False)
    return [line.strip() for line in out.splitlines() if line.strip()]


def snapshot(volume_name: str, snapshot_name: str, size_gb: int) -> str:
    """Create an LVM snapshot of a volume.
    
    size_gb is the COW (copy-on-write) extent size — defaults to 20% of source volume.
    Returns the snapshot device path.
    """
    vg = vg_name()
    _cleanup_stale_lv(snapshot_name, vg)
    _run(["lvcreate", "-y", "-W", "y", "-L", f"{size_gb}G", "-s", "-n", snapshot_name, f"{vg}/{volume_name}"] + LVM_CONTAINER_CFG)
    return f"/dev/{vg}/{snapshot_name}"
