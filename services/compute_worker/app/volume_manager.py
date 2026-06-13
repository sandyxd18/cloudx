from pathlib import Path

from . import libvirt_driver


def _target_dev(volume_id: str) -> str:
    letters = "bcdefghijklmnopqrstuvwxyz"
    index = sum(volume_id.encode("utf-8")) % len(letters)
    return f"sd{letters[index]}"


def attach_volume(payload: dict) -> dict:
    volume = payload["volume"]
    domain = payload.get("libvirt_domain_name")
    if not domain:
        raise RuntimeError("server has no libvirt domain name")
    path = Path(volume["path"])
    if not path.exists():
        raise RuntimeError(f"volume path does not exist: {path}")
    disk_format = "qcow2" if path.suffix == ".qcow2" else "raw"
    target = volume.get("target_dev") or _target_dev(volume["volume_id"])
    libvirt_driver.attach_disk(domain, path, target, disk_format)
    return {"target_dev": target, "path": str(path)}


def detach_volume(payload: dict) -> dict:
    volume = payload["volume"]
    domain = payload.get("libvirt_domain_name")
    if not domain:
        raise RuntimeError("server has no libvirt domain name")
    path = Path(volume["path"])
    disk_format = "qcow2" if path.suffix == ".qcow2" else "raw"
    target = volume.get("target_dev") or _target_dev(volume["volume_id"])
    libvirt_driver.detach_disk(domain, path, target, disk_format)
    return {"target_dev": target, "path": str(path)}
