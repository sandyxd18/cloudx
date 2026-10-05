"""
iSCSI export of LVM volumes (Linux LIO, driven through ``targetcli``).

A volume that lives on another node than the VM cannot be attached through a local path, so the storage node
exports the LV as an iSCSI LUN that only the initiator of the compute node that asked for it may log in to
(one target per volume, one ACL per initiator). The compute-worker does the login, see ``compute_worker/iscsi.py``.

Requires:
  - targetcli-fb on the storage node (``apt install targetcli-fb``)
  - ISCSI_PORTAL_IP: the node's management address that compute nodes reach (default: the address ``BLOCK_STORE_HOST`` resolves to)
"""

import logging
import os
import re
import socket

from shared.proc import output, run, succeeds

log = logging.getLogger(__name__)

IQN_PREFIX = os.getenv("ISCSI_IQN_PREFIX", "iqn.2026-10.io.cloudx")
PORTAL_PORT = 3260
TARGETCLI_TIMEOUT = 30


def target_iqn(volume_name: str) -> str:
    return f"{IQN_PREFIX}:{volume_name}"


def portal_ip() -> str:
    ip = os.getenv("ISCSI_PORTAL_IP") or socket.gethostbyname(os.getenv("BLOCK_STORE_HOST") or socket.gethostname())
    if ip.startswith("127."):
        raise RuntimeError("cannot tell which address compute nodes should use for iSCSI: set ISCSI_PORTAL_IP")
    return ip


def _tc(*args: str) -> list[str]:
    return ["targetcli", *args]


def _has(path: str) -> bool:
    return succeeds(_tc(path, "ls"), timeout=TARGETCLI_TIMEOUT)


def export(volume_name: str, device: str, initiator_iqn: str) -> dict:
    """Export *device* as LUN 0 of its own target and let *initiator_iqn* in. Idempotent."""
    iqn = target_iqn(volume_name)
    if not _has(f"/backstores/block/{volume_name}"):
        run(_tc("/backstores/block", "create", f"name={volume_name}", f"dev={device}"), timeout=TARGETCLI_TIMEOUT)
    if not _has(f"/iscsi/{iqn}"):
        run(_tc("/iscsi", "create", iqn), timeout=TARGETCLI_TIMEOUT)
    if not _has(f"/iscsi/{iqn}/tpg1/luns/lun0"):
        run(_tc(f"/iscsi/{iqn}/tpg1/luns", "create", f"/backstores/block/{volume_name}", "lun=0"), timeout=TARGETCLI_TIMEOUT)
    if not _has(f"/iscsi/{iqn}/tpg1/acls/{initiator_iqn}"):
        run(_tc(f"/iscsi/{iqn}/tpg1/acls", "create", initiator_iqn), timeout=TARGETCLI_TIMEOUT)
    run(_tc("saveconfig"), timeout=TARGETCLI_TIMEOUT)
    return {"target_iqn": iqn, "portal": f"{portal_ip()}:{PORTAL_PORT}", "lun": 0}


def unexport(volume_name: str, initiator_iqn: str) -> None:
    """Remove the initiator's access; tear the target down when nobody else is allowed in. Idempotent."""
    iqn = target_iqn(volume_name)
    if _has(f"/iscsi/{iqn}/tpg1/acls/{initiator_iqn}"):
        run(_tc(f"/iscsi/{iqn}/tpg1/acls", "delete", initiator_iqn), timeout=TARGETCLI_TIMEOUT)
    if _has(f"/iscsi/{iqn}"):
        listing = output(_tc(f"/iscsi/{iqn}/tpg1/acls", "ls"), timeout=TARGETCLI_TIMEOUT)
        remaining = re.search(r"\[ACLs: (\d+)\]", listing)
        if remaining and int(remaining.group(1)) == 0:
            run(_tc("/iscsi", "delete", iqn), timeout=TARGETCLI_TIMEOUT)
    if not _has(f"/iscsi/{iqn}") and _has(f"/backstores/block/{volume_name}"):
        run(_tc("/backstores/block", "delete", volume_name), timeout=TARGETCLI_TIMEOUT)
    run(_tc("saveconfig"), timeout=TARGETCLI_TIMEOUT)


def remove(volume_name: str) -> None:
    """Drop the target and backstore of a volume that is being deleted, whoever is still logged in."""
    iqn = target_iqn(volume_name)
    if _has(f"/iscsi/{iqn}"):
        run(_tc("/iscsi", "delete", iqn), timeout=TARGETCLI_TIMEOUT)
    if _has(f"/backstores/block/{volume_name}"):
        run(_tc("/backstores/block", "delete", volume_name), timeout=TARGETCLI_TIMEOUT)
    run(_tc("saveconfig"), timeout=TARGETCLI_TIMEOUT)
