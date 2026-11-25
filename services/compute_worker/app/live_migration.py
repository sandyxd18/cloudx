"""Live migration. Destination side: get this node ready to receive a running domain (prepare), give the migrated
taps their OVN identity once the domain runs here (finish), or undo whatever prepare made (rollback). Source side:
run() drives the whole thing.

The source worker drives the migration and calls the destination's three through RPC. The payload is the one the
server was built with (server_worker_payload: port, ports, ...) plus `_volumes`, a list of {volume_id, host, path,
backend, target_dev} (the root volume included), `_target_host` (from compute-api) and `_source_host` (added by run).
"""
import logging
import shutil
import threading
from pathlib import Path

from shared.rpc import host_queue

from . import cloudinit, iscsi, libvirt_driver, network_manager, volume_manager, worker
from .config import (
    COMPUTE_HOST, INSTANCE_DIR, LIVE_MIGRATION_AUTO_CONVERGE, LIVE_MIGRATION_BANDWIDTH_MBPS, LIVE_MIGRATION_HOST,
    LIVE_MIGRATION_MAX_DOWNTIME_MS, LIVE_MIGRATION_TIMEOUT, LIVE_MIGRATION_URI,
)

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


# ── Source side ───────────────────────────────────────────────────────────────

# RPC timeouts (seconds) of the calls to the destination. prepare may log in to several iSCSI targets; finish only sets
# OVS columns; rollback may undefine a domain and log out of / unexport several volumes.
PREPARE_TIMEOUT = 300
FINISH_TIMEOUT = 60
ROLLBACK_TIMEOUT = 120

# Servers this process is migrating now: a second live_migrate for one of them (a redelivered message) is ignored.
# The set lives in memory, so a worker restart empties it; recover_migrating_servers then frees the server.
_running: set[str] = set()
_running_lock = threading.Lock()


def _call(rpc, host: str, method: str, args: dict, timeout: int):
    """rpc.call to the compute worker of `host`. shared.rpc raises on an {"error": ...} reply and on a timeout; a reply
    in that shape is turned into an error here as well, so every failure of the step is an exception."""
    reply = rpc.call(host_queue("compute", host), method, args, timeout=timeout)
    if isinstance(reply, dict) and reply.get("error"):
        raise RuntimeError(f"{method} on {host} failed: {reply['error']}")
    return reply


def _source_disk_path(volume: dict) -> str | None:
    """The path the domain on this node opens the volume at: the iSCSI device when it is reached over a session,
    otherwise the volume's own path."""
    if _reaches_over_iscsi(volume):
        session = iscsi.find_session(volume["volume_id"])
        if session:
            return str(iscsi.device_path(*session))
    return volume.get("path") or None


def _disk_mapping(server_id: str, volumes: list[dict], disk_paths: dict) -> dict[str, str]:
    """Source path -> destination path, for the disks whose path differs between the two nodes."""
    mapping = {}
    for volume in volumes:
        volume_id = volume["volume_id"]
        source = _source_disk_path(volume)
        target = disk_paths.get(volume_id)
        if not source or not target:
            if source or target:
                log.warning("live migration of %s: volume %s has source path %r and destination path %r; "
                            "leaving its disk as it is", server_id, volume_id, source, target)
            continue
        if source != target:
            mapping[source] = target
    return mapping


def _remove_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


def run(args: dict, rpc, *, update_server, notify) -> None:
    """Live-migrate the server to args["_target_host"] and report the outcome. Never raises.

    The server is MIGRATING while this runs. On success its host becomes the target; on any failure before the
    domain has left, the target is rolled back and the server stays ACTIVE here; a failure in the cleanup after the
    domain has left is reported but does not roll back (the guest already runs on the target)."""
    server_id = _server_id(args)
    with _running_lock:
        if server_id in _running:
            log.warning("live migration of %s is already running here; ignoring the duplicate request", server_id)
            return
        _running.add(server_id)
    try:
        _run(args, rpc, update_server, notify)
    except Exception:  # _run reports its own failures; this is only so a thread never dies with a traceback
        log.exception("live migration of %s: unexpected error", server_id)
    finally:
        with _running_lock:
            _running.discard(server_id)


def _run(args: dict, rpc, update_server, notify) -> None:
    server_id = _server_id(args)
    target = args.get("_target_host")
    domain = args.get("libvirt_domain_name")
    if not server_id or not target or not domain:
        reason = "live migration payload needs a server id, _target_host and libvirt_domain_name"
        log.error("live migration of %s: %s", server_id, reason)
        if server_id:
            update_server(server_id, {"status": "ACTIVE", "error_message": None})
            notify(server_id, "error", reason, "live_migrate")
        return
    # the destination puts the config drive and console log at the same path, and rollback removes it there
    args = {**args, "instance_dir": str(_instance_dir(args)), "_source_host": COMPUTE_HOST}
    volumes = args.get("_volumes") or []
    log.info("live migration of %s (%s): %s -> %s", server_id, domain, COMPUTE_HOST, target)

    try:
        prepared = _call(rpc, target, "live_migrate_prepare", args, PREPARE_TIMEOUT)
        mapping = _disk_mapping(server_id, volumes, prepared.get("disk_paths") or {})
        dest_xml = libvirt_driver.rewrite_disk_sources(libvirt_driver.domain_xml_for_migration(domain), mapping)
        taps = libvirt_driver.interface_taps(domain)  # read now: the domain is gone from here once it has migrated
        libvirt_driver.migrate_domain(
            domain, LIVE_MIGRATION_URI.format(host=prepared.get("migration_host") or target), dest_xml,
            timeout=LIVE_MIGRATION_TIMEOUT, bandwidth_mbps=LIVE_MIGRATION_BANDWIDTH_MBPS,
            max_downtime_ms=LIVE_MIGRATION_MAX_DOWNTIME_MS, auto_converge=LIVE_MIGRATION_AUTO_CONVERGE,
        )
    except Exception as exc:
        log.error("live migration of %s to %s failed: %s; rolling back the target", server_id, target, exc)
        try:
            _call(rpc, target, "live_migrate_rollback", args, ROLLBACK_TIMEOUT)
        except Exception as rollback_exc:
            log.warning("live migration of %s: rollback on %s failed: %s", server_id, target, rollback_exc)
        update_server(server_id, {"status": "ACTIVE", "error_message": None})
        notify(server_id, "error", f"live migration failed: {exc}", "live_migrate")
        return

    log.info("live migration of %s: the domain runs on %s; cleaning up", server_id, target)
    failures = []

    def attempt(what: str, step, *step_args) -> None:
        try:
            step(*step_args)
        except Exception as exc:
            log.warning("live migration of %s: %s failed: %s", server_id, what, exc)
            failures.append(f"{what}: {exc}")

    # this node's taps go first, so two chassis never carry the same iface-id
    for tap in taps:
        attempt(f"delete tap {tap}", network_manager.delete_tap, tap)
    attempt(f"finish on {target}", _call, rpc, target, "live_migrate_finish", args, FINISH_TIMEOUT)
    for volume in volumes:
        if _reaches_over_iscsi(volume):
            attempt(f"disconnect volume {volume['volume_id']}", volume_manager.disconnect,
                    volume["volume_id"], volume.get("host"), rpc)
    attempt("remove the instance dir", _remove_dir, Path(args["instance_dir"]))
    update_server(server_id, {"host": target, "status": "ACTIVE", "error_message": None})
    if failures:
        notify(server_id, "error", "migrated, but cleanup failed: " + "; ".join(failures), "live_migrate")
    else:
        notify(server_id, "success", None, "live_migrate")
