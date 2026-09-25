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
# - global/udev_sync=0: don't wait for udev events
# - global/udev_rules=0: don't install udev rules
# - activation/udev_sync=0: don't sync LV activation with udev
# These prevent 'device not cleared' and udev-related hangs inside Docker.
# Format: global/key=0 is universally supported across LVM2 versions.
LVM_CONTAINER_CFG = [
    "--config",
    "global/udev_sync=0 global/udev_rules=0 activation/udev_sync=0 activation/udev_rules=0",
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
    Ensure the VG exists. If not, auto-recover or create from PVs/loopback backing file.
    Idempotent — safe to call on startup and reconciliation cycles.
    """
    vg = vg_name()

    # Refresh system LVM state
    subprocess.run(["pvscan"], capture_output=True)
    subprocess.run(["vgchange", "-ay", vg], capture_output=True)

    if _vg_exists(vg):
        log.info("LVM: VG '%s' active and ready", vg)
        return

    # Auto-recovery using loopback device if LVM_PV_NAME is not set or loopback is preferred
    img_path = f"/var/lib/{vg}.img"
    pvs = [p.strip() for p in LVM_PV_NAME.split(",") if p.strip()] if LVM_PV_NAME else []

    if not pvs:
        log.info("LVM: LVM_PV_NAME not set, initializing self-healing loop device at %s", img_path)
        if not os.path.exists(img_path):
            log.info("LVM: Creating 10GB backing file at %s", img_path)
            subprocess.run(["dd", "if=/dev/zero", f"of={img_path}", "bs=1M", "count=10240"], check=True)

        res = subprocess.run(["losetup", "-j", img_path], capture_output=True, text=True)
        loop_dev = res.stdout.split(":")[0].strip() if res.stdout else ""
        if not loop_dev:
            res_attach = subprocess.run(["losetup", "--find", "--show", img_path], capture_output=True, text=True, check=True)
            loop_dev = res_attach.stdout.strip()
            log.info("LVM: Attached %s to loop device %s", img_path, loop_dev)

        pvs = [loop_dev]

    log.info("LVM: Initializing VG '%s' from PVs: %s", vg, pvs)

    for pv in pvs:
        if not _pv_exists(pv):
            log.info("LVM: pvcreate %s", pv)
            _run(["pvcreate", "-y", pv])

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




def _thin_pool_exists(vg: str) -> bool:
    """Return True if a thin pool named '{VG}-pool' already exists in the VG."""
    out = _run(
        ["lvs", "--noheadings", "-o", "lv_name,lv_attr", vg],
        check=False,
    )
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == f"{vg}-pool" and "t" in parts[1]:
            return True
    return False


def ensure_thin_pool(vg: str) -> None:
    """Ensure a LVM thin pool named '{VG}-pool' exists.

    If not present, create it by allocating 80% of the VG's free PE extents.
    This is idempotent — safe to call on every thin volume create.
    """
    if _thin_pool_exists(vg):
        return

    # Get free extents in the VG
    out = _run(["vgs", "--noheadings", "-o", "vg_free_count", vg], check=False)
    out_stripped = out.strip()
    free_pe = int(out_stripped) if out_stripped.isdigit() else 0

    if free_pe < 4:
        raise RuntimeError(
            f"Not enough free space in VG '{vg}' to create a thin pool. "
            "Add more physical volumes or free up space."
        )

    # Use 80% of free PEs for the pool
    pool_pe = max(1, int(free_pe * 0.8))
    pool_name = f"{vg}-pool"

    log.info("LVM: creating thin pool %s/%s (%d PEs)", vg, pool_name, pool_pe)
    _run([
        "lvcreate", "-y",
        "--type", "thin-pool",
        "--extents", f"{pool_pe}",
        "-n", pool_name,
        vg,
    ] + LVM_CONTAINER_CFG)
    log.info("LVM: thin pool %s/%s created successfully", vg, pool_name)


def create(volume_name: str, size_gb: int, lvm_type: str = "default") -> str:
    """Create an LV and return its device path (/dev/VG/LV).

    lvm_type: 'default' (thick) or 'thin' (thin-provisioned from a thin pool).
    Thin provisioning requires a thin pool named '{VG}-pool' to exist.
    If the pool does not exist, we automatically create it using 80% of free VG space.
    """
    vg = vg_name()
    if lvm_type == "thin":
        ensure_thin_pool(vg)
        pool = f"{vg}-pool"
        _cleanup_stale_lv(volume_name, vg)
        _run([
            "lvcreate", "-y",
            "--virtualsize", f"{size_gb}G",
            "--thin",
            "--name", volume_name,
            f"{vg}/{pool}",
        ] + LVM_CONTAINER_CFG)
    else:
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
