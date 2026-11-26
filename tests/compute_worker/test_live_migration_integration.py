"""End to end on one process: the REAL live_migration.run drives the REAL prepare / finish / rollback through a fake
RPC `call` that dispatches into them "on" the target node. Only the edges are fakes: the libvirt driver, the volume
manager and iSCSI, the OVS/ip tap helpers, the config drive, and update_server / notify. Each fake keeps its state per
node, and the RPC switches the module's idea of "this node" while the target handler runs."""
import xml.etree.ElementTree as ET
from contextlib import contextmanager

import pytest

pytest.importorskip("fastapi")

from services.compute_worker.app import iscsi, libvirt_driver, live_migration, network_manager  # noqa: E402

SERVER = "11111111-2222-3333-4444-555555555555"
DOMAIN = "mini-os-vm1-11111111"
SOURCE, TARGET = "node-a", "node-b"
PORT = {"id": "p-main", "mac_address": "fa:16:3e:00:00:01", "logical_port_name": "lp-main"}
EXTRA = {"id": "p-extra", "mac_address": "fa:16:3e:00:00:02", "logical_port_name": "lp-extra"}
VOLUMES = [
    {"volume_id": "v-root", "host": None, "path": "rbd:volumes/v-root", "backend": "ceph", "target_dev": "sda"},
    {"volume_id": "v-remote", "host": "storage-1", "path": "/dev/vg/v-remote", "backend": "lvm", "target_dev": "sdb"},
    {"volume_id": "v-src", "host": SOURCE, "path": "/dev/vg/v-src", "backend": "lvm", "target_dev": "sdc"},
]
PORTALS = {"storage-1": "10.0.0.3:3260", SOURCE: "10.0.0.1:3260"}


def _session(volume_id, host):
    return PORTALS[host], f"iqn.2026-10.io.cloudx:{iscsi.lv_name(volume_id)}"


class Cloud:
    """Two compute nodes as seen by the fakes: domains, taps (with their OVS binding) and iSCSI sessions per node."""

    def __init__(self, monkeypatch, tmp_path):
        self.mp = monkeypatch
        self.node = SOURCE
        self.domains = {SOURCE: {DOMAIN: "RUNNING"}, TARGET: {}}
        self.taps = {SOURCE: {}, TARGET: {}}
        self.sessions = {SOURCE: {"v-remote": _session("v-remote", "storage-1")}, TARGET: {}}
        self.removed_dirs = []                       # (node, path): rmtree never really runs, the dir is shared here
        self.updates, self.notified = [], []
        self.bindings_at_migration = None
        self.migrate = "ok"                          # ok | fail | moved-then-raise
        self.fail_connect = set()
        for tap in (network_manager.tap_name(SERVER), network_manager.port_tap_name(SERVER, EXTRA["id"])):
            self.taps[SOURCE][tap] = {"binding": "bound-on-source"}
        self.instance_dir = tmp_path / "instances" / SERVER
        self.instance_dir.mkdir(parents=True)
        monkeypatch.setattr(live_migration, "INSTANCE_DIR", tmp_path / "instances")
        self._install()

    # --- which node the code believes it runs on ---------------------------------------------------------------
    def _as(self, node):
        self.node = node
        for module in (live_migration, live_migration.volume_manager):
            self.mp.setattr(module, "COMPUTE_HOST", node)
        self.mp.setattr(live_migration, "LIVE_MIGRATION_HOST", f"{node}.mig")

    @contextmanager
    def on(self, node):
        before = self.node
        self._as(node)
        try:
            yield
        finally:
            self._as(before)

    # --- the fakes ------------------------------------------------------------------------------------------------
    def domain_xml(self, name):
        disks = [f"<disk type='network' device='disk'><source protocol='rbd' name='volumes/v-root'/><target dev='sda'/></disk>",
                 f"<disk type='block' device='disk'><source dev='{iscsi.device_path(*self.sessions[SOURCE]['v-remote'])}'/><target dev='sdb'/></disk>",
                 "<disk type='block' device='disk'><source dev='/dev/vg/v-src'/><target dev='sdc'/></disk>"]
        nics = "".join(f"<interface type='ethernet'><target dev='{t}' managed='no'/></interface>" for t in self.taps[SOURCE])
        return f"<domain type='kvm'><name>{name}</name><devices>{''.join(disks)}{nics}</devices></domain>"

    def migrate_domain(self, name, uri, dest_xml, **kwargs):
        self.dest_xml, self.uri, self.migrate_kwargs = dest_xml, uri, kwargs
        self.bindings_at_migration = {t: dict(v) for t, v in self.taps[TARGET].items()}
        if self.migrate == "fail":
            raise libvirt_driver.MigrationError("unable to connect to the destination")
        del self.domains[SOURCE][name]
        self.domains[TARGET][name] = "RUNNING"
        if self.migrate == "moved-then-raise":
            raise libvirt_driver.MigrationError("End of file while reading data: Input/output error")

    def connect(self, volume, rpc):
        if volume["volume_id"] in self.fail_connect:
            raise RuntimeError(f"storage node refused to export volume {volume['volume_id']}")
        session = _session(volume["volume_id"], volume["host"])
        self.sessions[self.node][volume["volume_id"]] = session
        return iscsi.device_path(*session)

    def disconnect(self, volume_id, host, rpc):
        self.sessions[self.node].pop(volume_id, None)

    def _install(self):
        mp, ld, nm = self.mp, live_migration.libvirt_driver, live_migration.network_manager
        mp.setattr(ld, "domain_xml_for_migration", self.domain_xml)
        mp.setattr(ld, "interface_taps", lambda name: libvirt_driver.interface_taps_from_xml(self.domain_xml(name)))
        mp.setattr(ld, "migrate_domain", self.migrate_domain)
        mp.setattr(ld, "domain_state", lambda name: self.domains[self.node].get(name))
        mp.setattr(ld, "delete", lambda name: self.domains[self.node].pop(name, None))
        mp.setattr(nm, "create_tap", lambda tap: self.taps[self.node].__setitem__(tap, {"binding": None, "plugged": False}))
        mp.setattr(nm, "plug_tap", lambda tap: self.taps[self.node][tap].__setitem__("plugged", True))
        mp.setattr(nm, "set_tap_binding", lambda tap, lp, mac, sid: self.taps[self.node][tap].__setitem__("binding", (lp, mac, sid)))
        mp.setattr(nm, "delete_tap", lambda tap: self.taps[self.node].pop(tap, None))
        mp.setattr(live_migration.volume_manager, "connect", self.connect)
        mp.setattr(live_migration.volume_manager, "disconnect", self.disconnect)
        mp.setattr(live_migration.iscsi, "find_session", lambda volume_id: self.sessions[self.node].get(volume_id))
        mp.setattr(live_migration.iscsi, "initiator_iqn", lambda: f"iqn.2004-10.com.ubuntu:01:{self.node}")
        mp.setattr(live_migration.cloudinit, "create_config_drive", lambda d, *a, **k: d / "seed.iso")
        mp.setattr(live_migration.worker, "expose_to_host_libvirt", lambda *paths: None)
        mp.setattr(live_migration.shutil, "rmtree", lambda path, **kw: self.removed_dirs.append((self.node, str(path))))
        self._as(SOURCE)

    # --- the RPC: a `call` to the target's queue runs the real handler there ---------------------------------------
    def call(self, queue, method, args, timeout=None):
        assert queue.endswith(TARGET), queue
        handler = {"live_migrate_prepare": lambda: live_migration.prepare(args, self),
                   "live_migrate_finish": lambda: live_migration.finish(args),
                   "live_migrate_rollback": lambda: live_migration.rollback(args, self)}[method]
        with self.on(TARGET):
            try:
                return handler()
            except Exception as exc:  # shared.rpc turns a handler error into an {"error": ...} reply
                return {"error": str(exc)}

    def run(self):
        args = {"id": SERVER, "name": "vm1", "libvirt_domain_name": DOMAIN, "instance_dir": str(self.instance_dir),
                "port": PORT, "ports": [PORT, EXTRA], "_target_host": TARGET, "_volumes": [dict(v) for v in VOLUMES]}
        live_migration.run(args, self, update_server=lambda sid, fields: self.updates.append(fields),
                           notify=lambda sid, result, message, action, **extra: self.notified.append(
                               (result, message, action, extra.get("target_host"))))


@pytest.fixture
def cloud(monkeypatch, tmp_path):
    return Cloud(monkeypatch, tmp_path)


def _source_untouched(cloud):
    assert cloud.domains[SOURCE] == {DOMAIN: "RUNNING"}
    assert set(cloud.taps[SOURCE]) and all(t["binding"] == "bound-on-source" for t in cloud.taps[SOURCE].values())
    assert set(cloud.sessions[SOURCE]) == {"v-remote"}
    assert (SOURCE, str(cloud.instance_dir)) not in cloud.removed_dirs


def _target_clean(cloud):
    assert cloud.domains[TARGET] == {} and cloud.taps[TARGET] == {} and cloud.sessions[TARGET] == {}


def test_success_moves_the_guest_binds_the_target_taps_after_finish_and_cleans_the_source(cloud):
    cloud.run()

    assert cloud.domains == {SOURCE: {}, TARGET: {DOMAIN: "RUNNING"}}
    # while libvirt moved the guest the target taps were plugged but carried no OVN identity
    assert set(cloud.bindings_at_migration) == set(cloud.taps[TARGET]) and len(cloud.taps[TARGET]) == 2
    assert all(t == {"binding": None, "plugged": True} for t in cloud.bindings_at_migration.values())
    assert {t["binding"] for t in cloud.taps[TARGET].values()} == {("lp-main", PORT["mac_address"], SERVER),
                                                                   ("lp-extra", EXTRA["mac_address"], SERVER)}
    assert cloud.taps[SOURCE] == {}
    assert cloud.sessions == {SOURCE: {}, TARGET: {"v-remote": _session("v-remote", "storage-1"),
                                                   "v-src": _session("v-src", SOURCE)}}
    assert cloud.removed_dirs == [(SOURCE, str(cloud.instance_dir))]
    # the destination XML opens the source's own LV over iSCSI there; the shared storage-1 path and RBD stay
    sources = [s.get("dev") or s.get("name") for s in ET.fromstring(cloud.dest_xml).findall("./devices/disk/source")]
    assert sources == ["volumes/v-root", str(iscsi.device_path(*_session("v-remote", "storage-1"))),
                       str(iscsi.device_path(*_session("v-src", SOURCE)))]
    assert cloud.uri == "qemu+ssh://root@node-b.mig/system" and cloud.migrate_kwargs["migration_host"] == "node-b.mig"
    assert cloud.updates == [{"host": TARGET, "status": "ACTIVE", "error_message": None}]
    assert cloud.notified == [("success", None, "live_migrate", TARGET)]


def test_a_failed_migration_rolls_everything_back(cloud):
    cloud.migrate = "fail"

    cloud.run()

    _source_untouched(cloud)
    _target_clean(cloud)
    assert cloud.removed_dirs == [(TARGET, str(cloud.instance_dir))]
    assert cloud.updates == [{"status": "ACTIVE", "error_message": None}]
    ((result, message, action, target_host),) = cloud.notified
    assert (result, action, target_host) == ("error", "live_migrate", None)
    assert "unable to connect to the destination" in message


def test_a_prepare_failure_rolls_back_cleanly_and_never_migrates(cloud):
    cloud.fail_connect = {"v-src"}       # v-remote is logged in first, then the second login fails

    cloud.run()

    assert cloud.bindings_at_migration is None
    _source_untouched(cloud)
    _target_clean(cloud)
    assert cloud.updates == [{"status": "ACTIVE", "error_message": None}]
    ((result, message, _action, _target),) = cloud.notified
    assert result == "error" and "refused to export volume v-src" in message


def test_a_shared_initiator_name_fails_in_prepare_and_rolls_back(cloud, monkeypatch):
    monkeypatch.setattr(live_migration.iscsi, "initiator_iqn", lambda: "iqn.2004-10.com.ubuntu:01:cloned")

    cloud.run()

    assert cloud.bindings_at_migration is None
    _source_untouched(cloud)
    _target_clean(cloud)
    ((result, message, _action, _target),) = cloud.notified
    assert result == "error" and "same iSCSI initiator name" in message


def test_a_guest_that_moved_before_libvirt_raised_is_finished_not_rolled_back(cloud):
    cloud.migrate = "moved-then-raise"

    cloud.run()

    assert cloud.domains == {SOURCE: {}, TARGET: {DOMAIN: "RUNNING"}}
    assert len(cloud.taps[TARGET]) == 2 and all(t["binding"] for t in cloud.taps[TARGET].values())
    assert cloud.taps[SOURCE] == {} and cloud.sessions[SOURCE] == {}
    assert (TARGET, str(cloud.instance_dir)) not in cloud.removed_dirs
    assert cloud.updates == [{"host": TARGET, "status": "ACTIVE", "error_message": None}]
    ((result, message, _action, target_host),) = cloud.notified
    assert (result, target_host) == ("success", TARGET) and "End of file while reading data" in message
