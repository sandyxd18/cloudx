"""Live migration. Destination side: get this node ready to receive a running domain (prepare), give the migrated
taps their OVN identity once the domain runs here (finish), or undo whatever prepare made (rollback). Source side:
run() drives the whole thing.

The source worker drives the migration and calls the destination's three through RPC. The payload is the one the
server was built with (server_worker_payload: port, ports, ...) plus `_volumes`, a list of {volume_id, host, path,
backend, target_dev} (the root volume included), `_target_host` (from compute-api), and `_source_host` and
`_source_initiator_iqn` (added by run).
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
    """The server's instance directory: the same path as on the source, since the migrated domain XML points at the
    config drive and console log there. Prepare, rollback and the source cleanup delete it as root, so a value from the
    payload must lie strictly inside INSTANCE_DIR (after resolving `..` and symlinks); anything else is a ValueError."""
    if args.get("instance_dir"):
        path = Path(args["instance_dir"])
    elif _server_id(args):
        path = INSTANCE_DIR / _server_id(args)
    else:
        raise ValueError("live migration payload has neither instance_dir nor a server id")
    base, resolved = Path(INSTANCE_DIR).resolve(), path.resolve()
    if resolved == base or base not in resolved.parents:
        raise ValueError(f"live migration payload instance_dir {str(path)!r} is not inside {base}")
    return resolved


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


def _refuse_a_shared_initiator_name(args: dict) -> None:
    """LIO keeps one ACL per initiator name. Two nodes cloned from one template share /etc/iscsi/initiatorname.iscsi,
    and during a live migration both are logged in to the same LV: the source's logout and unexport afterwards would
    then cut the target's session too. Refuse before anything is made (the source then rolls back)."""
    source_iqn = args.get("_source_initiator_iqn")
    if not source_iqn or not any(_reaches_over_iscsi(v) for v in args.get("_volumes") or []):
        return
    own_iqn = iscsi.initiator_iqn()
    if own_iqn == source_iqn:
        raise RuntimeError(
            f"{COMPUTE_HOST} and {args.get('_source_host') or 'the source node'} have the same iSCSI initiator name "
            f"{own_iqn}; give every compute node its own (/etc/iscsi/initiatorname.iscsi, see docs/live-migration.md)")


def prepare(args: dict, rpc) -> dict:
    """Make this node ready for the domain: unbound taps on br-int, iSCSI sessions of the LVs that live elsewhere, and
    the config drive at the source's path. Repeating the call is harmless. If a step fails, what this call made is
    removed and the error is raised."""
    server_id = _server_id(args)
    instance_dir = _instance_dir(args)
    _refuse_a_shared_initiator_name(args)
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

    The server is MIGRATING while this runs. On success its host becomes the target; on a failure while the domain
    still runs here with no job left, the target is rolled back and the server goes back to ACTIVE here; when libvirt
    raised but the domain has left this node, the migration is finished as a success; a failure in the cleanup after
    the domain has left is reported but does not roll back (the guest already runs on the target). When it cannot be
    told where the guest runs, nothing is rolled back or reported: the server stays MIGRATING with an error_message."""
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


def _report(server_id: str, fields: dict, result: str, message: str | None, update_server, notify, **extra) -> None:
    """Set the server's fields through the conductor, then notify compute-api of the outcome. The notify always goes
    out with the real result, even when the status update fails (a cast raises while RabbitMQ is down); compute-api
    then sets host and status itself from the notify (`extra` carries the target host on success)."""
    try:
        update_server(server_id, fields)
    except Exception as exc:
        log.error("live migration of %s: could not update the server to %s: %s", server_id, fields, exc)
        note = f"the status update failed ({exc})"
        message = f"{message}; {note}" if message else note
    notify(server_id, result, message, "live_migrate", **extra)


def _own_initiator_iqn() -> str | None:
    """This node's iSCSI initiator name, or None on a node without open-iscsi (RBD only)."""
    try:
        return iscsi.initiator_iqn()
    except Exception as exc:
        log.debug("no iSCSI initiator name on this node: %s", exc)
        return None


def _outcome_after_failure(domain: str, exc: Exception) -> str:
    """Where is the guest after migrate_domain raised? "source": the job has ended and the domain still runs here, so
    the target can be rolled back. "target": the domain has left this node (libvirt raised after the destination took
    over, e.g. the connection dropped in the confirm step). "unclear": the migration call may still be running, the
    lookup failed, or the domain is here but not running; rolling back could destroy the only running copy."""
    if getattr(exc, "still_running", False):
        return "unclear"
    try:
        state = libvirt_driver.domain_state(domain)
    except Exception as lookup_exc:
        log.error("live migration: could not look up %s on this node after the failure: %s", domain, lookup_exc)
        return "unclear"
    if state is None:
        return "target"
    return "source" if state == "RUNNING" else "unclear"


def _run(args: dict, rpc, update_server, notify) -> None:
    server_id = _server_id(args)
    target = args.get("_target_host")
    domain = args.get("libvirt_domain_name")
    reason = None
    if not server_id or not target or not domain:
        reason = "live migration payload needs a server id, _target_host and libvirt_domain_name"
    else:
        try:  # the destination puts the config drive and console log at the same path, and rollback removes it there
            args = {**args, "instance_dir": str(_instance_dir(args)), "_source_host": COMPUTE_HOST,
                    "_source_initiator_iqn": _own_initiator_iqn()}
        except ValueError as exc:
            reason = str(exc)
    if reason:
        log.error("live migration of %s: %s", server_id, reason)
        if server_id:
            _report(server_id, {"status": "ACTIVE", "error_message": None}, "error", reason, update_server, notify)
        return
    volumes = args.get("_volumes") or []
    log.info("live migration of %s (%s): %s -> %s", server_id, domain, COMPUTE_HOST, target)

    migrating = False  # set once migrate_domain is entered: from then on the guest may have left this node
    try:
        prepared = _call(rpc, target, "live_migrate_prepare", args, PREPARE_TIMEOUT)
        mapping = _disk_mapping(server_id, volumes, prepared.get("disk_paths") or {})
        dest_xml = libvirt_driver.rewrite_disk_sources(libvirt_driver.domain_xml_for_migration(domain), mapping)
        taps = libvirt_driver.interface_taps(domain)  # read now: the domain is gone from here once it has migrated
        # one address for both the libvirt connection and the QEMU memory stream (VIR_MIGRATE_PARAM_URI)
        migration_host = prepared.get("migration_host") or target
        migrating = True
        libvirt_driver.migrate_domain(
            domain, LIVE_MIGRATION_URI.format(host=migration_host), dest_xml,
            timeout=LIVE_MIGRATION_TIMEOUT, bandwidth_mbps=LIVE_MIGRATION_BANDWIDTH_MBPS,
            max_downtime_ms=LIVE_MIGRATION_MAX_DOWNTIME_MS, auto_converge=LIVE_MIGRATION_AUTO_CONVERGE,
            migration_host=migration_host,
        )
        moved_despite = None
    except Exception as exc:
        outcome = _outcome_after_failure(domain, exc) if migrating else "source"
        if outcome == "unclear":
            note = (f"live migration to {target} failed ({exc}) and it is unclear where the guest runs; the server stays "
                    f"MIGRATING: inspect both nodes (virsh domjobinfo / virsh list) before any recovery step")
            log.error("live migration of %s: %s", server_id, note)
            try:
                update_server(server_id, {"error_message": note})
            except Exception as update_exc:
                log.error("live migration of %s: could not record the error message: %s", server_id, update_exc)
            return
        if outcome == "source":
            log.error("live migration of %s to %s failed: %s; rolling back the target", server_id, target, exc)
            # No race with a prepare that timed out: the target worker has one consumer with prefetch 1
            # (shared/rpc.py RPCConsumer) and acks after the handler, so its queue runs prepare to the end before it
            # delivers this rollback (an unacked prepare is redelivered ahead of it after a target worker restart).
            try:
                _call(rpc, target, "live_migrate_rollback", args, ROLLBACK_TIMEOUT)
            except Exception as rollback_exc:
                log.warning("live migration of %s: rollback on %s failed: %s", server_id, target, rollback_exc)
            _report(server_id, {"status": "ACTIVE", "error_message": None}, "error", f"live migration failed: {exc}",
                    update_server, notify)
            return
        log.warning("live migration of %s: libvirt reported %s, but the domain has left this node; finishing the "
                    "migration on %s", server_id, exc, target)
        moved_despite = exc

    log.info("live migration of %s: the domain runs on %s; cleaning up", server_id, target)
    failures = [f"libvirt reported an error after the guest had moved ({moved_despite})"] if moved_despite else []

    def attempt(what: str, step, *step_args) -> None:
        try:
            step(*step_args)
        except Exception as exc:
            log.warning("live migration of %s: %s failed: %s", server_id, what, exc)
            failures.append(f"{what}: {exc}")

    # this node's taps go first, so two chassis never carry the same iface-id
    for tap in taps:
        attempt(f"delete tap {tap}", network_manager.delete_tap, tap)
    cleaned = len(failures)
    attempt(f"finish on {target}", _call, rpc, target, "live_migrate_finish", args, FINISH_TIMEOUT)
    finish_failed = len(failures) > cleaned
    for volume in volumes:
        if _reaches_over_iscsi(volume):
            attempt(f"disconnect volume {volume['volume_id']}", volume_manager.disconnect,
                    volume["volume_id"], volume.get("host"), rpc)
    attempt("remove the instance dir", _remove_dir, Path(args["instance_dir"]))
    # The guest runs on the target whatever failed above, so the result is "success": compute-api moves the placement
    # claim back to the source on "error", which would be wrong for a server that now lives on the target.
    message = None
    if failures:
        message = "migrated, but cleanup failed: " + "; ".join(failures)
        if finish_failed:
            message += (f"; the guest's network may be down until an operator re-binds its ports on {target}")
    _report(server_id, {"host": target, "status": "ACTIVE", "error_message": None}, "success", message,
            update_server, notify, target_host=target)
