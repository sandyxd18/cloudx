"""Live migration, destination side: get this node ready to receive a running domain (prepare), give the migrated
taps their OVN identity once the domain runs here (finish), or undo whatever prepare made (rollback).

The source worker drives the migration and calls these three through RPC. The payload is the one the server was
built with (server_worker_payload: port, ports, ...) plus `_volumes`, a list of {volume_id, host, path, backend,
target_dev} (the root volume included), and `_source_host`.
"""
import logging
import shutil
from pathlib import Path

from . import cloudinit, libvirt_driver, network_manager, volume_manager, worker
from .config import COMPUTE_HOST, INSTANCE_DIR, LIVE_MIGRATION_HOST

log = logging.getLogger("compute-worker")


def _server_id(args: dict) -> str:
    return args.get("id") or args.get("server_id")


def _instance_dir(args: dict) -> Path:
    # the same path as on the source: the migrated domain XML points at the config drive and console log there
    if args.get("instance_dir"):
        return Path(args["instance_dir"])
    if not _server_id(args):
        raise ValueError("live migration payload has neither instance_dir nor a server id")  # never INSTANCE_DIR itself
    return INSTANCE_DIR / _server_id(args)


def _taps(args: dict) -> list[tuple[str, dict]]:
    """(tap, port) of the primary port and then of each extra port."""
    server_id = _server_id(args)
    port, extra_ports, interfaces = worker._nics_of(server_id, args)
    primary = args.get("tap_name") or network_manager.tap_name(server_id)
    return [(primary, port), *[(tap, extra) for (tap, _mac), extra in zip(interfaces, extra_ports)]]


def _logical_port(port: dict) -> str:
    return port.get("logical_port_name") or port.get("name")


def _reaches_over_iscsi(volume: dict) -> bool:
    """An LV on another storage node. An RBD image is opened over Ceph on every node, and an LV on this node is local."""
    host = volume.get("host")
    return bool(host) and host != COMPUTE_HOST and not volume_manager._is_rbd(volume.get("path"))


def prepare(args: dict, rpc) -> dict:
    """Make this node ready for the domain: unbound taps on br-int, iSCSI sessions of the LVs that live elsewhere, and
    the config drive at the source's path. Repeating the call is harmless. If a step fails, what this call made is
    removed and the error is raised."""
    server_id = _server_id(args)
    instance_dir = _instance_dir(args)
    dir_existed = instance_dir.exists()
    taps, connected, disk_paths = [], [], {}
    try:
        instance_dir.mkdir(parents=True, exist_ok=True)
        # no iface-id yet: the source still owns the OVN port, and two chassis claiming it would make OVN flap
        for tap, _port in _taps(args):
            network_manager.create_tap(tap)
            taps.append(tap)
            network_manager.plug_tap(tap)
        for volume in args.get("_volumes") or []:
            volume_id, path = volume["volume_id"], volume.get("path")
            if _reaches_over_iscsi(volume):
                disk_paths[volume_id] = str(volume_manager.connect(volume, rpc))
                connected.append(volume)
            elif path:  # nothing to open locally when the path is empty
                disk_paths[volume_id] = path
            else:
                log.warning("live migration of %s: volume %s has no path; leaving it out of disk_paths", server_id, volume_id)
        port, extra_ports, _ = worker._nics_of(server_id, args)
        seed_iso = cloudinit.create_config_drive(
            instance_dir, server_id, args.get("name", ""), args.get("public_key_snapshot"), port,
            args.get("user_data"), args.get("keypair_name"), extra_ports=extra_ports,
        )
        console_log = instance_dir / "console.log"
        console_log.touch(exist_ok=True)
        console_log.chmod(0o666)
        worker.expose_to_host_libvirt(instance_dir, seed_iso, console_log)
    except Exception:
        for tap in taps:
            network_manager.delete_tap(tap)
        for volume in connected:
            try:
                volume_manager.disconnect(volume["volume_id"], volume.get("host"), rpc)
            except Exception as exc:
                log.warning("prepare cleanup: could not disconnect volume %s: %s", volume["volume_id"], exc)
        if not dir_existed:
            shutil.rmtree(instance_dir, ignore_errors=True)
        raise
    return {"disk_paths": disk_paths, "migration_host": LIVE_MIGRATION_HOST}


def finish(args: dict) -> dict:
    """The domain runs here: give each tap the OVN identity (iface-id and the rest) so the port moves to this chassis."""
    server_id = _server_id(args)
    for tap, port in _taps(args):
        mac = port.get("mac_address") or args.get("mac_address")
        network_manager.set_tap_binding(tap, _logical_port(port), mac, server_id)
    return {"status": "ok"}


def rollback(args: dict, rpc) -> dict:
    """Undo prepare (and a domain libvirt may have started here). Best effort: every step runs, none can raise."""
    def attempt(what: str, step, *step_args) -> None:
        try:
            step(*step_args)
        except Exception as exc:
            log.warning("live migration rollback: %s failed: %s", what, exc)

    domain = args.get("libvirt_domain_name")
    if domain:
        attempt(f"delete domain {domain}", libvirt_driver.delete, domain)
    try:
        taps = [tap for tap, _port in _taps(args)]
    except Exception as exc:
        log.warning("live migration rollback: could not work out the tap names: %s", exc)
        taps = []
    for tap in taps:
        attempt(f"delete tap {tap}", network_manager.delete_tap, tap)
    for volume in args.get("_volumes") or []:
        if _reaches_over_iscsi(volume):
            attempt(f"disconnect volume {volume.get('volume_id')}", volume_manager.disconnect, volume["volume_id"], volume.get("host"), rpc)
    try:
        shutil.rmtree(_instance_dir(args), ignore_errors=True)
    except Exception as exc:
        log.warning("live migration rollback: could not remove the instance dir: %s", exc)
    return {"status": "ok"}
