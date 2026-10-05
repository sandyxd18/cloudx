"""
iSCSI initiator side of cross-node LVM volumes (open-iscsi; the target side is block_store_worker/drivers/iscsi.py).

A volume whose LV lives on another storage node is logged in here, and the resulting block device is handed to libvirt
exactly like a local LV. Requires ``open-iscsi`` on the compute node (``apt install open-iscsi``).
"""
import re
import time
from pathlib import Path

from shared.proc import run

INITIATOR_FILE = Path("/etc/iscsi/initiatorname.iscsi")
ISCSIADM_TIMEOUT = 60
DEVICE_WAIT_SECONDS = 20
_ALREADY_EXISTS = 15  # iscsiadm: session/record already present


def lv_name(volume_id: str) -> str:
    """LV (and target) name of a volume; must stay identical to block_store_worker's _lv_name."""
    return f"vol-{volume_id.replace('-', '')[:24]}"


def initiator_iqn() -> str:
    match = re.search(r"^InitiatorName=(\S+)", INITIATOR_FILE.read_text(), re.MULTILINE)
    if not match:
        raise RuntimeError(f"no InitiatorName in {INITIATOR_FILE}; is open-iscsi installed?")
    return match.group(1)


def device_path(portal: str, target_iqn: str, lun: int = 0) -> Path:
    return Path(f"/dev/disk/by-path/ip-{portal}-iscsi-{target_iqn}-lun-{lun}")


def login(portal: str, target_iqn: str, lun: int = 0) -> Path:
    """Log in to the target and return the block device once udev has created it. Idempotent."""
    node = ["iscsiadm", "-m", "node", "-T", target_iqn, "-p", portal]
    if run([*node, "-o", "new"], check=False, timeout=ISCSIADM_TIMEOUT).returncode not in (0, _ALREADY_EXISTS):
        run([*node, "-o", "new"], timeout=ISCSIADM_TIMEOUT)  # raises with the real error
    if run([*node, "--login"], check=False, timeout=ISCSIADM_TIMEOUT).returncode not in (0, _ALREADY_EXISTS):
        run([*node, "--login"], timeout=ISCSIADM_TIMEOUT)
    dev = device_path(portal, target_iqn, lun)
    deadline = time.monotonic() + DEVICE_WAIT_SECONDS
    while not dev.exists():
        if time.monotonic() > deadline:
            raise RuntimeError(f"{dev} did not appear after iSCSI login")
        time.sleep(0.5)
    return dev


def find_session(volume_id: str) -> tuple[str, str] | None:
    """(portal, target iqn) of the active session of a volume, if any."""
    name = lv_name(volume_id)
    listing = run(["iscsiadm", "-m", "session"], check=False, timeout=ISCSIADM_TIMEOUT).stdout or ""
    for line in listing.splitlines():
        # tcp: [3] 192.168.10.12:3260,1 iqn.2026-10.io.cloudx:vol-abc (non-flash)
        match = re.search(r"\s(\S+?:\d+),\d+\s+(\S+:" + re.escape(name) + r")\b", line)
        if match:
            return match.group(1), match.group(2)
    return None


def logout(portal: str, target_iqn: str) -> None:
    node = ["iscsiadm", "-m", "node", "-T", target_iqn, "-p", portal]
    run([*node, "--logout"], check=False, timeout=ISCSIADM_TIMEOUT)
    run([*node, "-o", "delete"], check=False, timeout=ISCSIADM_TIMEOUT)

