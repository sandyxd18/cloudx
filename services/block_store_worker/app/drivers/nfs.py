"""
NFS volume driver for cloudx block storage.

Requires:
  - STORAGE_NFS_DRIVER=true in env
  - STORAGE_NFS_DIR: local mount path of the NFS share (default: /var/lib/cloudx/volumes)
  - qemu-img available on host

Volumes are stored as qcow2 files in {STORAGE_NFS_DIR}/{volume_name}.qcow2.
Both storage node and compute nodes mount the same NFS export at {STORAGE_NFS_DIR},
allowing compute hypervisors (libvirt) to attach disks directly via shared filesystem.
"""

import logging
import os
import shutil
from pathlib import Path

from shared.proc import run

log = logging.getLogger(__name__)

STORAGE_NFS_DIR = os.getenv("STORAGE_NFS_DIR", "/var/lib/cloudx/volumes")
QEMU_IMG_TIMEOUT = 120  # create / resize; whole-image copies are unlimited


def _ensure_dir() -> Path:
    p = Path(STORAGE_NFS_DIR)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _vol_path(name: str) -> Path:
    return _ensure_dir() / f"{name}.qcow2"


def create(volume_name: str, size_gb: int, **kwargs) -> str:
    """Create a new qcow2 volume file in the NFS directory."""
    path = _vol_path(volume_name)
    if path.exists():
        log.warning("NFS: volume file %s already exists, overwriting", path)
        path.unlink()

    cmd = ["qemu-img", "create", "-f", "qcow2", str(path), f"{size_gb}G"]
    run(cmd, timeout=QEMU_IMG_TIMEOUT)

    log.info("NFS: created volume %s at %s (%dGB)", volume_name, path, size_gb)
    return str(path)


def create_from_source(volume_name: str, source_path: str, size_gb: int) -> str:
    """Convert source image to qcow2 and resize to size_gb."""
    path = _vol_path(volume_name)
    if path.exists():
        path.unlink()

    cmd = ["qemu-img", "convert", "-O", "qcow2", source_path, str(path)]
    run(cmd, timeout=None)  # copies the whole image

    # Resize to requested volume size
    res_resize = run(["qemu-img", "resize", str(path), f"{size_gb}G"], check=False, timeout=QEMU_IMG_TIMEOUT)
    if res_resize.returncode != 0:
        log.warning("qemu-img resize warning: %s", res_resize.stderr)

    log.info("NFS: created volume %s from %s at %s (%dGB)", volume_name, source_path, path, size_gb)
    return str(path)


def delete(volume_name: str) -> None:
    """Delete a volume file."""
    path = _vol_path(volume_name)
    if path.exists():
        path.unlink()
        log.info("NFS: deleted volume %s (%s)", volume_name, path)
    else:
        log.warning("NFS: delete requested for non-existent volume %s", path)


def extend(volume_name: str, new_size_gb: int) -> None:
    """Resize volume file to new_size_gb."""
    path = _vol_path(volume_name)
    if not path.exists():
        raise RuntimeError(f"volume file not found: {path}")

    cmd = ["qemu-img", "resize", str(path), f"{new_size_gb}G"]
    run(cmd, timeout=QEMU_IMG_TIMEOUT)

    log.info("NFS: extended volume %s to %dGB", volume_name, new_size_gb)


def snapshot(volume_name: str, snap_name: str, cow_gb: int = 1) -> str:
    """Create a linked qcow2 snapshot or copy."""
    src_path = _vol_path(volume_name)
    if not src_path.exists():
        raise RuntimeError(f"volume file not found: {src_path}")

    snap_path = _vol_path(snap_name)
    if snap_path.exists():
        snap_path.unlink()

    # Create linked snapshot
    cmd = ["qemu-img", "create", "-f", "qcow2", "-b", src_path.name, "-F", "qcow2", str(snap_path)]
    res = run(cmd, cwd=src_path.parent, check=False, timeout=QEMU_IMG_TIMEOUT)
    if res.returncode != 0:
        # Fallback to copy if linked fails
        shutil.copy2(src_path, snap_path)

    log.info("NFS: created snapshot %s at %s", snap_name, snap_path)
    return str(snap_path)


def clone(source_path: str, snap_name: str, new_volume: str) -> str:
    """Clone an existing image/snapshot to a new volume file."""
    dst_path = _vol_path(new_volume)
    if dst_path.exists():
        dst_path.unlink()

    cmd = ["qemu-img", "convert", "-O", "qcow2", source_path, str(dst_path)]
    run(cmd, timeout=None)  # copies the whole image

    log.info("NFS: cloned %s to %s", source_path, dst_path)
    return str(dst_path)
