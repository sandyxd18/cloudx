"""Source side of live migration: run() drives prepare on the target, the libvirt migration, and then either the
cleanup of the source (success) or a rollback of the target (failure). main.handle_live_migrate runs it in a thread."""
import threading

import pytest

pytest.importorskip("fastapi")

from services.compute_worker.app import live_migration, main  # noqa: E402
from shared.rpc import host_queue  # noqa: E402

SERVER = "11111111-2222-3333-4444-555555555555"
DOMAIN = "mini-os-vm1-11111111"
RBD = "rbd:volumes/v-rbd"
# the source reaches v-remote (on storage-1) and v-b (on the target node-b) over iSCSI
SRC_REMOTE = "/dev/disk/by-path/ip-10.0.0.3:3260-iscsi-iqn.x:volume-v-remote-lun-0"
SRC_B = "/dev/disk/by-path/ip-10.0.0.2:3260-iscsi-iqn.x:volume-v-b-lun-0"
SESSIONS = {"v-remote": ("10.0.0.3:3260", "iqn.x:volume-v-remote"), "v-b": ("10.0.0.2:3260", "iqn.x:volume-v-b")}
# what prepare on node-b answers: node-b reaches the source's own LV over iSCSI, its own LV locally, v-remote over the
# same storage-1 session path as the source, RBD unchanged; v-empty (no path) is left out
DISK_PATHS = {
    "v-root": "/dev/disk/by-path/ip-10.0.0.1:3260-iscsi-iqn.x:volume-v-root-lun-0",
    "v-remote": SRC_REMOTE,
    "v-b": "/dev/vg/volume-v-b",
    "v-rbd": RBD,
}

DOMAIN_XML = f"""<domain type='kvm'><name>{DOMAIN}</name><devices>
<disk type='block' device='disk'><source dev='/dev/vg/volume-v-root'/><target dev='vda'/></disk>
<disk type='block' device='disk'><source dev='{SRC_REMOTE}'/><target dev='sdb'/></disk>
<disk type='block' device='disk'><source dev='{SRC_B}'/><target dev='sdc'/></disk>
<disk type='network' device='disk'><source protocol='rbd' name='volumes/v-rbd'/><target dev='sdd'/></disk>
<interface type='ethernet'><target dev='tap-a'/></interface>
<interface type='ethernet'><target dev='tap-b'/></interface>
</devices></domain>"""


class FakeRPC:
    def __init__(self, f):
        self.f = f
        self.replies = {"live_migrate_prepare": {"disk_paths": DISK_PATHS, "migration_host": "node-b.mig"}}
        self.raises = {}

    def call(self, queue, method, args, timeout=None):
        self.f.log(method, queue, args, timeout)
        if method in self.raises:
            raise self.raises[method]
        return self.replies.get(method, {"status": "ok"})


class Fakes:
    def __init__(self):
        self.calls = []
        self.migrate_error = None
        self.migrate_gate = None  # threading.Event that migrate_domain waits on
        self.migrate_entered = threading.Event()
        self.mappings = []

    def log(self, *call):
        self.calls.append(call)

    def of(self, name):
        return [c[1:] for c in self.calls if c[0] == name]

    def names(self):
        return [c[0] for c in self.calls]

    def update_server(self, server_id, fields):
        self.log("update_server", server_id, fields)

    def notify(self, server_id, result, message, action):
        self.log("notify", server_id, result, message, action)


@pytest.fixture
def fakes(monkeypatch, tmp_path):
    f = Fakes()
    monkeypatch.setattr(live_migration, "COMPUTE_HOST", "node-a")
    monkeypatch.setattr(live_migration, "LIVE_MIGRATION_URI", "qemu+ssh://root@{host}/system")
    monkeypatch.setattr(live_migration, "LIVE_MIGRATION_TIMEOUT", 1800)
    monkeypatch.setattr(live_migration, "LIVE_MIGRATION_BANDWIDTH_MBPS", 100)
    monkeypatch.setattr(live_migration, "LIVE_MIGRATION_MAX_DOWNTIME_MS", 300)
    monkeypatch.setattr(live_migration, "LIVE_MIGRATION_AUTO_CONVERGE", False)
    ld = live_migration.libvirt_driver
    monkeypatch.setattr(ld, "interface_taps", lambda name: f.log("interface_taps", name) or ["tap-a", "tap-b"])
    monkeypatch.setattr(ld, "domain_xml_for_migration", lambda name: f.log("domain_xml", name) or DOMAIN_XML)
    real_rewrite = ld.rewrite_disk_sources

    def rewrite(xml, mapping):
        f.mappings.append(dict(mapping))
        return real_rewrite(xml, mapping)

    monkeypatch.setattr(ld, "rewrite_disk_sources", rewrite)

    def migrate_domain(name, dest_uri, dest_xml, **kwargs):
        f.log("migrate_domain", name, dest_uri, dest_xml, kwargs)
        f.migrate_entered.set()
        if f.migrate_gate is not None:
            f.migrate_gate.wait(5)
        if f.migrate_error is not None:
            raise f.migrate_error

    monkeypatch.setattr(ld, "migrate_domain", migrate_domain)
    monkeypatch.setattr(live_migration.network_manager, "delete_tap", lambda tap: f.log("delete_tap", tap))
    monkeypatch.setattr(live_migration.iscsi, "find_session", lambda volume_id: SESSIONS.get(volume_id))
    monkeypatch.setattr(live_migration.volume_manager, "disconnect", lambda vid, host, rpc: f.log("disconnect", vid, host))
    f.rpc = FakeRPC(f)
    f.instance_dir = tmp_path / "instances" / SERVER
    f.instance_dir.mkdir(parents=True)
    (f.instance_dir / "seed.iso").write_text("x")
    return f


def _args(f, volumes=None):
    return {
        "id": SERVER,
        "name": "vm1",
        "libvirt_domain_name": DOMAIN,
        "instance_dir": str(f.instance_dir),
        "port": {"id": "p-main", "mac_address": "fa:16:3e:00:00:01"},
        "_target_host": "node-b",
        "_volumes": volumes if volumes is not None else [
            {"volume_id": "v-root", "host": "node-a", "path": "/dev/vg/volume-v-root", "backend": "lvm", "target_dev": "vda"},
            {"volume_id": "v-remote", "host": "storage-1", "path": "/dev/vg/volume-v-remote", "backend": "lvm", "target_dev": "sdb"},
            {"volume_id": "v-b", "host": "node-b", "path": "/dev/vg/volume-v-b", "backend": "lvm", "target_dev": "sdc"},
            {"volume_id": "v-rbd", "host": None, "path": RBD, "backend": "ceph", "target_dev": "sdd"},
            {"volume_id": "v-empty", "host": "node-a", "path": "", "backend": "lvm", "target_dev": "sde"},
        ],
    }


def _run(f, args=None):
    live_migration.run(args or _args(f), f.rpc, update_server=f.update_server, notify=f.notify)


def _updates(f):
    return [fields for (_sid, fields) in f.of("update_server")]


def _notified(f):
    return [(result, message, action) for (_sid, result, message, action) in f.of("notify")]


def test_success_order(fakes):
    _run(fakes)

    steps = [c for c in fakes.calls if c[0] in (
        "live_migrate_prepare", "migrate_domain", "delete_tap", "live_migrate_finish", "disconnect", "update_server",
        "notify", "live_migrate_rollback")]
    assert [s[0] for s in steps] == [
        "live_migrate_prepare", "migrate_domain", "delete_tap", "delete_tap", "live_migrate_finish",
        "disconnect", "disconnect", "update_server", "notify",
    ]
    assert [t for (t,) in fakes.of("delete_tap")] == ["tap-a", "tap-b"]
    assert sorted(fakes.of("disconnect")) == [("v-b", "node-b"), ("v-remote", "storage-1")]
    assert _updates(fakes) == [{"host": "node-b", "status": "ACTIVE", "error_message": None}]
    assert _notified(fakes) == [("success", None, "live_migrate")]
    assert not fakes.instance_dir.exists()
    # the taps were read before the domain left this node
    assert fakes.names().index("interface_taps") < fakes.names().index("migrate_domain")


def test_prepare_is_called_on_the_target_queue_with_the_source_host(fakes):
    _run(fakes)

    ((queue, args, timeout),) = fakes.of("live_migrate_prepare")
    assert queue == host_queue("compute", "node-b")
    assert timeout == live_migration.PREPARE_TIMEOUT == 300
    assert args["_source_host"] == "node-a"
    assert args["libvirt_domain_name"] == DOMAIN and args["instance_dir"] == str(fakes.instance_dir)
    ((queue, args, _timeout),) = fakes.of("live_migrate_finish")
    assert queue == host_queue("compute", "node-b")
    assert args["libvirt_domain_name"] == DOMAIN and args["instance_dir"] == str(fakes.instance_dir)


def test_migrate_domain_uses_the_configured_values(fakes):
    _run(fakes)

    ((name, uri, _xml, kwargs),) = fakes.of("migrate_domain")
    assert name == DOMAIN
    assert uri == "qemu+ssh://root@node-b.mig/system"
    assert kwargs == {"timeout": 1800, "bandwidth_mbps": 100, "max_downtime_ms": 300, "auto_converge": False}


def test_dest_xml_uses_the_target_paths_only_for_changed_disks(fakes):
    _run(fakes)

    assert fakes.mappings == [{
        "/dev/vg/volume-v-root": DISK_PATHS["v-root"],
        SRC_B: "/dev/vg/volume-v-b",
    }]
    ((_name, _uri, dest_xml, _kw),) = fakes.of("migrate_domain")
    assert DISK_PATHS["v-root"] in dest_xml and "/dev/vg/volume-v-b" in dest_xml
    assert SRC_REMOTE in dest_xml  # unchanged: the same storage-1 session path on both nodes
    assert "volumes/v-rbd" in dest_xml and "/dev/vg/volume-v-root" not in dest_xml


def test_a_volume_left_out_of_disk_paths_is_skipped(fakes, caplog):
    fakes.rpc.replies["live_migrate_prepare"] = {"disk_paths": {"v-rbd": RBD}, "migration_host": "node-b.mig"}
    volumes = [
        {"volume_id": "v-empty", "host": "node-a", "path": "", "backend": "lvm", "target_dev": "vda"},
        {"volume_id": "v-none", "host": None, "path": None, "backend": "lvm", "target_dev": "vdb"},
        {"volume_id": "v-rbd", "host": None, "path": RBD, "backend": "ceph", "target_dev": "sdd"},
        {"volume_id": "v-root", "host": "node-a", "path": "/dev/vg/volume-v-root", "backend": "lvm", "target_dev": "vdc"},
    ]

    with caplog.at_level("WARNING", logger="compute-worker"):
        _run(fakes, _args(fakes, volumes))

    assert fakes.mappings == [{}]
    assert _notified(fakes) == [("success", None, "live_migrate")]
    assert "v-root" in caplog.text  # a volume with a source path but no target path is logged


def test_prepare_failure_changes_nothing_on_the_source(fakes):
    fakes.rpc.raises["live_migrate_prepare"] = RuntimeError("RPC live_migrate_prepare error: export refused")

    _run(fakes)

    assert fakes.of("migrate_domain") == []
    assert fakes.of("delete_tap") == [] and fakes.of("disconnect") == []
    assert len(fakes.of("live_migrate_rollback")) == 1
    assert _updates(fakes) == [{"status": "ACTIVE", "error_message": None}]
    ((result, message, action),) = _notified(fakes)
    assert result == "error" and "export refused" in message and action == "live_migrate"
    assert fakes.instance_dir.exists()


def test_prepare_error_reply_is_a_failure(fakes):
    fakes.rpc.replies["live_migrate_prepare"] = {"error": "no space on node-b"}

    _run(fakes)

    assert fakes.of("migrate_domain") == []
    assert len(fakes.of("live_migrate_rollback")) == 1
    assert _updates(fakes) == [{"status": "ACTIVE", "error_message": None}]
    ((result, message, _action),) = _notified(fakes)
    assert result == "error" and "no space on node-b" in message


def test_prepare_timeout_is_a_failure(fakes):
    fakes.rpc.raises["live_migrate_prepare"] = TimeoutError("RPC call compute.node-b.live_migrate_prepare timed out after 300s")

    _run(fakes)

    assert fakes.of("migrate_domain") == []
    ((queue, args, _timeout),) = fakes.of("live_migrate_rollback")
    assert queue == host_queue("compute", "node-b")
    assert args["libvirt_domain_name"] == DOMAIN and args["instance_dir"] == str(fakes.instance_dir)
    ((result, message, _action),) = _notified(fakes)
    assert result == "error" and "timed out" in message


def test_migration_failure_rolls_back_the_target(fakes):
    fakes.migrate_error = live_migration.libvirt_driver.MigrationError("timed out after 1800 s")

    _run(fakes)

    assert len(fakes.of("live_migrate_rollback")) == 1
    assert fakes.of("delete_tap") == []
    assert fakes.of("live_migrate_finish") == [] and fakes.of("disconnect") == []
    assert _updates(fakes) == [{"status": "ACTIVE", "error_message": None}]
    assert all("host" not in u for u in _updates(fakes))
    ((result, message, _action),) = _notified(fakes)
    assert result == "error" and "timed out after 1800 s" in message
    assert fakes.instance_dir.exists()


def test_finish_failure_after_a_successful_migration_does_not_roll_back(fakes):
    fakes.rpc.raises["live_migrate_finish"] = RuntimeError("RPC live_migrate_finish error: ovs-vsctl failed")

    _run(fakes)

    assert fakes.of("live_migrate_rollback") == []
    assert len(fakes.of("disconnect")) == 2  # the rest of the cleanup still runs
    assert _updates(fakes) == [{"host": "node-b", "status": "ACTIVE", "error_message": None}]
    ((result, message, action),) = _notified(fakes)
    assert result == "error" and "cleanup" in message and "ovs-vsctl failed" in message and action == "live_migrate"


def test_rollback_failure_is_logged_and_the_status_is_still_restored(fakes, caplog):
    fakes.migrate_error = live_migration.libvirt_driver.MigrationError("connection reset")
    fakes.rpc.raises["live_migrate_rollback"] = TimeoutError("rollback timed out")

    with caplog.at_level("WARNING", logger="compute-worker"):
        _run(fakes)

    assert "rollback timed out" in caplog.text
    assert _updates(fakes) == [{"status": "ACTIVE", "error_message": None}]
    ((result, message, _action),) = _notified(fakes)
    assert result == "error" and "connection reset" in message


def test_a_payload_without_a_target_or_domain_fails_without_touching_anything(fakes):
    args = _args(fakes)
    del args["_target_host"]

    _run(fakes, args)

    assert fakes.of("live_migrate_prepare") == [] and fakes.of("live_migrate_rollback") == []
    assert _updates(fakes) == [{"status": "ACTIVE", "error_message": None}]
    assert _notified(fakes)[0][0] == "error"


def test_a_second_call_for_the_same_server_does_not_start_a_second_migration(fakes):
    fakes.migrate_gate = threading.Event()
    first = threading.Thread(target=_run, args=(fakes,))
    first.start()
    assert fakes.migrate_entered.wait(5)

    _run(fakes)  # a duplicate while the first one runs: returns at once and changes nothing

    assert len(fakes.of("live_migrate_prepare")) == 1
    assert fakes.of("update_server") == [] and fakes.of("notify") == []
    fakes.migrate_gate.set()
    first.join(5)
    assert not first.is_alive()
    assert len(fakes.of("migrate_domain")) == 1
    assert _notified(fakes) == [("success", None, "live_migrate")]


def test_the_guard_is_released_after_a_failure(fakes):
    fakes.migrate_error = live_migration.libvirt_driver.MigrationError("boom")
    _run(fakes)
    fakes.migrate_error = None

    _run(fakes)  # a retry (or a call after a worker restart left the server ACTIVE again) starts a new migration

    assert len(fakes.of("live_migrate_prepare")) == 2
    assert _notified(fakes)[-1] == ("success", None, "live_migrate")


def test_handler_runs_in_a_thread(monkeypatch):
    started, release, done = threading.Event(), threading.Event(), threading.Event()
    seen = {}

    def fake_run(args, rpc, *, update_server, notify):
        seen.update(args=args, update_server=update_server, notify=notify)
        started.set()
        release.wait(5)
        done.set()

    class FakeClient:
        def __init__(self, *a, **k):
            self.closed = False

        def close(self):
            self.closed = True

    monkeypatch.setattr(main.live_migration, "run", fake_run)
    monkeypatch.setattr(main, "RPCClient", FakeClient)

    assert main.handle_live_migrate({"id": SERVER, "_target_host": "node-b"}) is None

    assert started.wait(5)
    assert not done.is_set()  # the handler returned while run was still busy
    release.set()
    assert done.wait(5)
    assert seen["args"]["id"] == SERVER
    assert seen["update_server"] is main.conductor_update_server and seen["notify"] is main._notify_action_finish


def test_live_migrate_is_the_handler_key():
    assert main.HANDLERS["live_migrate"] is main.handle_live_migrate
    assert "live_migrate_instance" not in main.HANDLERS
