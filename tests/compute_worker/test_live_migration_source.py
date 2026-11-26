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
        self.target_hosts = []
        self.action_ids = []
        self.source_state = "RUNNING"  # what domain_state answers for the source domain after a failed migration

    def log(self, *call):
        self.calls.append(call)

    def of(self, name):
        return [c[1:] for c in self.calls if c[0] == name]

    def names(self):
        return [c[0] for c in self.calls]

    def update_server(self, server_id, fields):
        self.log("update_server", server_id, fields)

    def notify(self, server_id, result, message, action, target_host=None, action_id=None):
        self.log("notify", server_id, result, message, action)
        self.target_hosts.append(target_host)
        self.action_ids.append(action_id)


@pytest.fixture
def fakes(monkeypatch, tmp_path):
    f = Fakes()
    monkeypatch.setattr(live_migration, "COMPUTE_HOST", "node-a")
    monkeypatch.setattr(live_migration.volume_manager, "COMPUTE_HOST", "node-a")  # the remote-volume rule reads this one
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

    def domain_state(name):
        f.log("domain_state", name)
        if isinstance(f.source_state, Exception):
            raise f.source_state
        return f.source_state

    monkeypatch.setattr(ld, "domain_state", domain_state)
    monkeypatch.setattr(live_migration.iscsi, "initiator_iqn", lambda: "iqn.2004-10.com.ubuntu:01:node-a")
    monkeypatch.setattr(live_migration.network_manager, "delete_tap", lambda tap: f.log("delete_tap", tap))
    monkeypatch.setattr(live_migration.iscsi, "find_session", lambda volume_id: SESSIONS.get(volume_id))
    monkeypatch.setattr(live_migration.volume_manager, "disconnect", lambda vid, host, rpc: f.log("disconnect", vid, host))
    f.rpc = FakeRPC(f)
    monkeypatch.setattr(live_migration, "INSTANCE_DIR", tmp_path / "instances")
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
    assert kwargs == {"timeout": 1800, "bandwidth_mbps": 100, "max_downtime_ms": 300, "auto_converge": False,
                      "migration_host": "node-b.mig"}


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
    # the guest runs on the target: "success", so compute-api does not move the placement claim back to the source
    assert result == "success" and action == "live_migrate"
    assert message.startswith("migrated, but cleanup failed: ") and "ovs-vsctl failed" in message
    assert "network may be down" in message


def test_source_cleanup_failure_notifies_success_without_the_network_warning(fakes, monkeypatch):
    def delete_tap(tap):
        fakes.log("delete_tap", tap)
        raise RuntimeError("ovs-vsctl del-port timed out")

    monkeypatch.setattr(live_migration.network_manager, "delete_tap", delete_tap)

    _run(fakes)

    assert fakes.of("live_migrate_rollback") == []
    assert len(fakes.of("live_migrate_finish")) == 1  # the rest of the cleanup still runs
    assert _updates(fakes) == [{"host": "node-b", "status": "ACTIVE", "error_message": None}]
    ((result, message, _action),) = _notified(fakes)
    assert result == "success" and message.startswith("migrated, but cleanup failed: ")
    assert "del-port timed out" in message and "network may be down" not in message


def test_a_failed_status_update_after_success_still_notifies_success(fakes, monkeypatch):
    def update_server(server_id, fields):
        fakes.log("update_server", server_id, fields)
        raise RuntimeError("Failed to cast update_server after 3 attempts")

    monkeypatch.setattr(fakes, "update_server", update_server)

    _run(fakes)

    ((result, message, action),) = _notified(fakes)
    assert result == "success" and action == "live_migrate"
    assert "status update failed" in message and "Failed to cast" in message


def test_a_failed_status_update_after_a_rollback_still_notifies_the_error(fakes, monkeypatch):
    fakes.migrate_error = live_migration.libvirt_driver.MigrationError("connection reset")

    def update_server(server_id, fields):
        fakes.log("update_server", server_id, fields)
        raise RuntimeError("Failed to cast update_server after 3 attempts")

    monkeypatch.setattr(fakes, "update_server", update_server)

    _run(fakes)

    assert len(fakes.of("live_migrate_rollback")) == 1
    ((result, message, _action),) = _notified(fakes)
    assert result == "error" and "connection reset" in message and "status update failed" in message


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


# --- final-review fixes -------------------------------------------------------------------------------------------

def test_success_notify_carries_the_target_host(fakes):
    _run(fakes)

    assert fakes.target_hosts == ["node-b"]  # compute-api sets host/status itself if the conductor cast was lost


def test_the_memory_stream_falls_back_to_the_target_name(fakes):
    fakes.rpc.replies["live_migrate_prepare"] = {"disk_paths": DISK_PATHS}

    _run(fakes)

    ((_name, uri, _xml, kwargs),) = fakes.of("migrate_domain")
    assert uri == "qemu+ssh://root@node-b/system" and kwargs["migration_host"] == "node-b"


def test_prepare_gets_the_source_initiator_name(fakes):
    _run(fakes)

    ((_queue, args, _timeout),) = fakes.of("live_migrate_prepare")
    assert args["_source_initiator_iqn"] == "iqn.2004-10.com.ubuntu:01:node-a"


def test_a_source_without_open_iscsi_sends_no_initiator_name(fakes, monkeypatch):
    def missing():
        raise FileNotFoundError("/etc/iscsi/initiatorname.iscsi")

    monkeypatch.setattr(live_migration.iscsi, "initiator_iqn", missing)

    _run(fakes)

    ((_queue, args, _timeout),) = fakes.of("live_migrate_prepare")
    assert args["_source_initiator_iqn"] is None
    assert _notified(fakes) == [("success", None, "live_migrate")]


def test_a_prepare_failure_rolls_back_without_looking_at_the_source_domain(fakes):
    fakes.rpc.raises["live_migrate_prepare"] = RuntimeError("export refused")

    _run(fakes)

    assert fakes.of("domain_state") == []
    assert len(fakes.of("live_migrate_rollback")) == 1


def test_a_failed_migration_with_the_source_domain_running_rolls_back(fakes):
    fakes.migrate_error = live_migration.libvirt_driver.MigrationError("connection reset")

    _run(fakes)

    assert fakes.of("domain_state") == [(DOMAIN,)]
    assert len(fakes.of("live_migrate_rollback")) == 1
    assert _notified(fakes)[0][0] == "error"


def test_a_failed_migration_whose_guest_already_left_takes_the_success_path(fakes):
    # migrateToURI3 raised after the destination resumed (the connection dropped in the confirm step): the source
    # domain is gone, so the guest runs on the target and must not be rolled back
    fakes.migrate_error = live_migration.libvirt_driver.MigrationError("End of file while reading data")
    fakes.source_state = None

    _run(fakes)

    assert fakes.of("live_migrate_rollback") == []
    assert [t for (t,) in fakes.of("delete_tap")] == ["tap-a", "tap-b"]
    assert len(fakes.of("live_migrate_finish")) == 1
    assert _updates(fakes) == [{"host": "node-b", "status": "ACTIVE", "error_message": None}]
    ((result, message, action),) = _notified(fakes)
    assert result == "success" and action == "live_migrate"
    assert "End of file while reading data" in message
    assert fakes.target_hosts == ["node-b"]
    assert not fakes.instance_dir.exists()


UNCLEAR = [
    ("call still running", live_migration.libvirt_driver.MigrationError("timed out", still_running=True), "RUNNING"),
    ("source lookup fails", live_migration.libvirt_driver.MigrationError("connection reset"), RuntimeError("libvirtd down")),
    ("source domain paused", live_migration.libvirt_driver.MigrationError("connection reset"), "PAUSED"),
]


@pytest.mark.parametrize("error,state", [u[1:] for u in UNCLEAR], ids=[u[0] for u in UNCLEAR])
def test_an_unclear_outcome_neither_rolls_back_nor_reports(fakes, caplog, error, state):
    fakes.migrate_error = error
    fakes.source_state = state

    with caplog.at_level("ERROR", logger="compute-worker"):
        _run(fakes)

    assert fakes.of("live_migrate_rollback") == [] and fakes.of("live_migrate_finish") == []
    assert fakes.of("delete_tap") == [] and fakes.of("disconnect") == []
    assert fakes.of("notify") == []                      # no claim restore, no status change by compute-api
    (fields,) = _updates(fakes)
    assert set(fields) == {"error_message"}               # status stays MIGRATING for an operator
    assert "inspect" in fields["error_message"] and "MIGRATING" in fields["error_message"]
    assert "live migration of" in caplog.text
    assert fakes.instance_dir.exists()


@pytest.mark.parametrize("bad", ["escape", "elsewhere", "root"])
def test_an_instance_dir_outside_the_instances_dir_is_refused(fakes, tmp_path, bad):
    # never real system paths: a bug here would rmtree them
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("x")
    value = {"escape": str(fakes.instance_dir / ".." / ".." / "outside"), "elsewhere": str(outside),
             "root": str(tmp_path / "instances")}[bad]
    args = _args(fakes)
    args["instance_dir"] = value

    _run(fakes, args)

    assert fakes.of("live_migrate_prepare") == [] and fakes.of("migrate_domain") == []
    assert _updates(fakes) == [{"status": "ACTIVE", "error_message": None}]
    ((result, message, _action),) = _notified(fakes)
    assert result == "error" and "instance_dir" in message
    assert (outside / "keep").exists() and fakes.instance_dir.exists()


def test_notify_sends_the_target_host_only_when_given(monkeypatch):
    sent = []
    monkeypatch.setattr(main.catalog, "url", lambda name: "http://compute.test:8003")
    monkeypatch.setattr(main.requests, "post", lambda url, json=None, **kw: sent.append((url, json)))

    main._notify_action_finish(SERVER, "success", None, "live_migrate", target_host="node-b")
    main._notify_action_finish(SERVER, "error", "boom", "shelve")

    assert sent[0] == (f"http://compute.test:8003/internal/servers/{SERVER}/actions/finish",
                       {"result": "success", "message": None, "action": "live_migrate", "target_host": "node-b"})
    assert sent[1][1] == {"result": "error", "message": "boom", "action": "shelve"}  # unchanged for other actions


# --- follow-ups: a domain that vanished before the job started (A6), IPv6 in the URI template (A5) -------------

@pytest.mark.parametrize("error", [
    live_migration.libvirt_driver.MigrationError("domain not found", job_started=False),
    RuntimeError("Domain not found: no domain with matching name"),   # lookup in migrate_domain, before any call
], ids=["migration error before the call", "raw lookup error"])
def test_a_domain_gone_before_the_job_started_is_unclear_not_a_success(fakes, error):
    fakes.migrate_error = error
    fakes.source_state = None

    _run(fakes)

    assert fakes.of("live_migrate_rollback") == [] and fakes.of("live_migrate_finish") == []
    assert fakes.of("notify") == []
    (fields,) = _updates(fakes)
    assert set(fields) == {"error_message"} and "unclear" in fields["error_message"]


def test_the_libvirt_uri_brackets_an_ipv6_migration_host(fakes):
    fakes.rpc.replies["live_migrate_prepare"] = {"disk_paths": {}, "migration_host": "fd00::b"}

    _run(fakes)

    ((_name, dest_uri, _xml, kwargs),) = fakes.of("migrate_domain")
    assert dest_uri == "qemu+ssh://root@[fd00::b]/system"
    assert kwargs["migration_host"] == "fd00::b"   # migrate_domain brackets the memory stream URI itself


@pytest.mark.parametrize("fail", [False, True], ids=["success", "rolled back"])
def test_the_report_names_the_action_row_compute_api_sent(fakes, fail):
    if fail:
        fakes.rpc.raises["live_migrate_prepare"] = RuntimeError("export refused")
    args = {**_args(fakes), "_action_id": "act-7"}

    live_migration.run(args, fakes.rpc, update_server=fakes.update_server, notify=fakes.notify)

    assert fakes.action_ids == ["act-7"]
