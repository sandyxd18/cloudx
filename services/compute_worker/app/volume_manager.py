import logging
from pathlib import Path

from shared.rpc import host_queue

from . import iscsi, libvirt_driver
from .config import COMPUTE_HOST

log = logging.getLogger(__name__)

EXPORT_TIMEOUT = 60


def _target_dev(volume_id: str) -> str:
    letters = "bcdefghijklmnopqrstuvwxyz"
    index = sum(volume_id.encode("utf-8")) % len(letters)
    return f"sd{letters[index]}"


def _is_remote(volume: dict) -> bool:
    return bool(volume.get("host")) and volume["host"] != COMPUTE_HOST


def connect(volume: dict, rpc) -> Path:
    """Block device of *volume* on this node: its own path, or an iSCSI login when the LV lives on another storage node."""
    if not _is_remote(volume):
        return Path(volume["path"])
    volume_id = volume.get("volume_id") or volume["id"]
    info = rpc.call(
        host_queue("block-store", volume["host"]), "export_volume",
        {"volume_id": volume_id, "initiator_iqn": iscsi.initiator_iqn()}, timeout=EXPORT_TIMEOUT,
    )
    if info.get("error"):
        raise RuntimeError(f"storage node {volume['host']} refused to export volume {volume_id}: {info['error']}")
    return iscsi.login(info["portal"], info["target_iqn"], info.get("lun", 0))


def disconnect(volume_id: str, host: str | None, rpc) -> None:
    """Log out of the volume's iSCSI session (if it has one) and tell its storage node to stop exporting it."""
    session = iscsi.find_session(volume_id)
    if session:
        iscsi.logout(*session)
    if host and host != COMPUTE_HOST:
        reply = rpc.call(
            host_queue("block-store", host), "unexport_volume",
            {"volume_id": volume_id, "initiator_iqn": iscsi.initiator_iqn()}, timeout=EXPORT_TIMEOUT,
        )
        if reply.get("error"):
            log.warning("storage node %s could not unexport %s: %s", host, volume_id, reply["error"])


def attach_volume(payload: dict, rpc) -> dict:
    volume = payload["volume"]
    domain = payload.get("libvirt_domain_name")
    if not domain:
        raise RuntimeError("server has no libvirt domain name")
    path = connect(volume, rpc)
    if not path.exists():
        raise RuntimeError(f"volume path does not exist: {path}")
    disk_format = "qcow2" if path.suffix == ".qcow2" else "raw"
    target = volume.get("target_dev") or _target_dev(volume["volume_id"])
    try:
        libvirt_driver.attach_disk(domain, path, target, disk_format)
    except Exception:
        if _is_remote(volume):
            disconnect(volume["volume_id"], volume["host"], rpc)
        raise
    return {"target_dev": target, "path": str(path)}


def detach_volume(payload: dict, rpc) -> dict:
    volume = payload["volume"]
    domain = payload.get("libvirt_domain_name")
    if not domain:
        raise RuntimeError("server has no libvirt domain name")
    session = iscsi.find_session(volume["volume_id"]) if _is_remote(volume) else None
    path = iscsi.device_path(*session) if session else Path(volume["path"])
    disk_format = "qcow2" if path.suffix == ".qcow2" else "raw"
    target = volume.get("target_dev") or _target_dev(volume["volume_id"])
    libvirt_driver.detach_disk(domain, path, target, disk_format)
    if _is_remote(volume):
        disconnect(volume["volume_id"], volume["host"], rpc)
    return {"target_dev": target, "path": str(path)}
