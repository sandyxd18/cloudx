"""Target side of live migration: prepare (taps without OVN identity, iSCSI sessions, config drive), finish (bind the
taps) and rollback (undo whatever prepare made, never raising)."""
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from services.compute_worker.app import live_migration, main  # noqa: E402

SERVER = "11111111-2222-3333-4444-555555555555"
RBD = "rbd:vms/root-disk"


class Fakes:
    """Records every call the target side makes into the worker's helpers."""

    def __init__(self):
        self.calls = []
        self.fail_connect = set()
        self.fail_disconnect = set()
        self.domain_deleted = []

    def log(self, *call):
        self.calls.append(call)

    def of(self, name):
        return [c[1:] for c in self.calls if c[0] == name]


@pytest.fixture
def fakes(monkeypatch, tmp_path):
    f = Fakes()
    monkeypatch.setattr(live_migration, "COMPUTE_HOST", "node-b")
    monkeypatch.setattr(live_migration, "LIVE_MIGRATION_HOST", "node-b.mig")
    nm = live_migration.network_manager
    monkeypatch.setattr(nm, "create_tap", lambda n: f.log("create_tap", n))
    monkeypatch.setattr(nm, "plug_tap", lambda n: f.log("plug_tap", n))
    monkeypatch.setattr(nm, "set_tap_binding", lambda *a: f.log("set_tap_binding", *a))
    monkeypatch.setattr(nm, "delete_tap", lambda n: f.log("delete_tap", n))
    vm = live_migration.volume_manager

    def connect(volume, rpc):
        f.log("connect", volume["volume_id"])
        if volume["volume_id"] in f.fail_connect:
            raise RuntimeError("export refused")
        if vm._is_remote(volume):
            return Path(f"/dev/disk/by-path/iscsi-{volume['volume_id']}")
        return Path(volume["path"])

    def disconnect(volume_id, host, rpc):
        f.log("disconnect", volume_id, host)
        if volume_id in f.fail_disconnect:
            raise RuntimeError("storage node down")

    monkeypatch.setattr(vm, "connect", connect)
    monkeypatch.setattr(vm, "disconnect", disconnect)
    monkeypatch.setattr(vm, "COMPUTE_HOST", "node-b")

    def create_config_drive(instance_dir, *a, **k):
        f.log("create_config_drive", instance_dir, a, k)
        return Path(instance_dir) / "seed.iso"

    monkeypatch.setattr(live_migration.cloudinit, "create_config_drive", create_config_drive)
    monkeypatch.setattr(live_migration.worker, "expose_to_host_libvirt", lambda *p: f.log("expose", *p))
    monkeypatch.setattr(live_migration.libvirt_driver, "delete", lambda n: f.domain_deleted.append(n))
    monkeypatch.setattr(live_migration, "INSTANCE_DIR", tmp_path / "instances")
    monkeypatch.setattr(live_migration.iscsi, "initiator_iqn", lambda: f.own_iqn)
    f.own_iqn = "iqn.2004-10.com.ubuntu:01:node-b"
    f.instance_dir = tmp_path / "instances" / SERVER
    return f


def _args(fakes, volumes=None):
    return {
        "id": SERVER,
        "name": "vm1",
        "libvirt_domain_name": "mini-os-vm1-11111111",
        "instance_dir": str(fakes.instance_dir),
        "public_key_snapshot": "ssh-ed25519 AAA",
        "keypair_name": "k1",
        "port": {"id": "p-main", "mac_address": "fa:16:3e:00:00:01", "logical_port_name": "lp-main"},
        "ports": [
            {"id": "p-main", "mac_address": "fa:16:3e:00:00:01", "logical_port_name": "lp-main"},
            {"id": "p-extra", "mac_address": "fa:16:3e:00:00:02", "name": "extra-name"},
        ],
        "_source_host": "node-a",
        "_volumes": volumes if volumes is not None else [
            {"volume_id": "v-root", "host": None, "path": RBD, "backend": "ceph", "target_dev": "sda"},
            {"volume_id": "v-remote", "host": "storage-1", "path": "/dev/vg/v-remote", "backend": "lvm", "target_dev": "sdb"},
            {"volume_id": "v-local", "host": "node-b", "path": "/dev/vg/v-local", "backend": "lvm", "target_dev": "sdc"},
        ],
    }


def test_prepare_plugs_taps_without_binding_and_returns_disk_paths(fakes):
    result = live_migration.prepare(_args(fakes), rpc=object())

    taps = [t for (t,) in fakes.of("create_tap")]
    assert len(taps) == 2 and taps[0] != taps[1]
    assert [t for (t,) in fakes.of("plug_tap")] == taps
    assert fakes.of("set_tap_binding") == []
    assert result == {
        "disk_paths": {
            "v-root": RBD,
            "v-remote": "/dev/disk/by-path/iscsi-v-remote",
            "v-local": "/dev/vg/v-local",
        },
        "migration_host": "node-b.mig",
    }
    assert fakes.of("connect") == [("v-remote",)]  # RBD and this node's own LV need no session


def test_prepare_recreates_the_config_drive_at_the_same_path(fakes):
    live_migration.prepare(_args(fakes), rpc=object())

    ((instance_dir, args, kwargs),) = fakes.of("create_config_drive")
    assert instance_dir == fakes.instance_dir
    assert args[0] == SERVER and args[1] == "vm1"
    assert [p["id"] for p in kwargs["extra_ports"]] == ["p-extra"]
    assert fakes.instance_dir.is_dir()
    assert fakes.instance_dir / "seed.iso" in fakes.of("expose")[0]


def test_prepare_failure_cleans_what_it_made(fakes):
    volumes = [
        {"volume_id": "v-remote", "host": "storage-1", "path": "/dev/vg/a", "backend": "lvm", "target_dev": "sdb"},
        {"volume_id": "v-remote2", "host": "storage-2", "path": "/dev/vg/b", "backend": "lvm", "target_dev": "sdc"},
    ]
    fakes.fail_connect = {"v-remote2"}

    with pytest.raises(RuntimeError, match="export refused"):
        live_migration.prepare(_args(fakes, volumes), rpc=object())

    made = [t for (t,) in fakes.of("create_tap")]
    assert sorted(t for (t,) in fakes.of("delete_tap")) == sorted(made) and made
    assert ("v-remote", "storage-1") in fakes.of("disconnect")
    assert not fakes.instance_dir.exists()  # prepare made it, so it takes it away


def test_prepare_failure_keeps_an_instance_dir_it_did_not_create(fakes):
    fakes.instance_dir.mkdir(parents=True)
    fakes.fail_connect = {"v-remote"}

    with pytest.raises(RuntimeError):
        live_migration.prepare(_args(fakes), rpc=object())

    assert fakes.instance_dir.is_dir()


def test_prepare_twice_is_harmless(fakes):
    first = live_migration.prepare(_args(fakes), rpc=object())
    second = live_migration.prepare(_args(fakes), rpc=object())

    assert second == first
    assert fakes.of("set_tap_binding") == []


def test_prepare_handles_a_volume_with_an_empty_path(fakes):
    volumes = [
        {"volume_id": "v-root", "host": None, "path": RBD, "backend": "ceph", "target_dev": "sda"},
        {"volume_id": "v-nopath-local", "host": "node-b", "path": "", "backend": "lvm", "target_dev": "sdb"},
        {"volume_id": "v-nopath-remote", "host": "storage-1", "path": "", "backend": "lvm", "target_dev": "sdc"},
        {"volume_id": "v-nopath-none", "host": None, "path": None, "backend": "lvm", "target_dev": "sdd"},
    ]

    result = live_migration.prepare(_args(fakes, volumes), rpc=object())

    # nothing local to point at for an empty path; a remote LV is reached over iSCSI whatever its recorded path
    assert result["disk_paths"] == {"v-root": RBD, "v-nopath-remote": "/dev/disk/by-path/iscsi-v-nopath-remote"}


def test_finish_binds_every_tap(fakes):
    result = live_migration.finish(_args(fakes))

    assert result == {"status": "ok"}
    bound = fakes.of("set_tap_binding")
    assert len(bound) == 2
    by_logical = {logical: (mac, sid) for (_tap, logical, mac, sid) in bound}
    assert by_logical == {
        "lp-main": ("fa:16:3e:00:00:01", SERVER),
        "extra-name": ("fa:16:3e:00:00:02", SERVER),
    }
    assert len({tap for (tap, *_rest) in bound}) == 2


def test_rollback_removes_taps_sessions_and_the_instance_dir_and_never_raises(fakes):
    fakes.instance_dir.mkdir(parents=True)
    (fakes.instance_dir / "seed.iso").write_text("x")
    fakes.fail_disconnect = {"v-remote"}
    volumes = [
        {"volume_id": "v-root", "host": None, "path": RBD, "backend": "ceph", "target_dev": "sda"},
        {"volume_id": "v-remote", "host": "storage-1", "path": "/dev/vg/a", "backend": "lvm", "target_dev": "sdb"},
        {"volume_id": "v-remote2", "host": "storage-2", "path": "/dev/vg/b", "backend": "lvm", "target_dev": "sdc"},
        {"volume_id": "v-local", "host": "node-b", "path": "/dev/vg/l", "backend": "lvm", "target_dev": "sdd"},
    ]

    result = live_migration.rollback(_args(fakes, volumes), rpc=object())

    assert result == {"status": "ok"}
    assert len(fakes.of("delete_tap")) == 2
    assert sorted(fakes.of("disconnect")) == [("v-remote", "storage-1"), ("v-remote2", "storage-2")]
    assert not fakes.instance_dir.exists()
    assert fakes.domain_deleted == ["mini-os-vm1-11111111"]


def test_rollback_is_safe_when_prepare_never_ran(fakes, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("nothing here")

    monkeypatch.setattr(live_migration.network_manager, "delete_tap", boom)
    monkeypatch.setattr(live_migration.libvirt_driver, "delete", boom)
    args = _args(fakes)
    del args["libvirt_domain_name"]
    del args["_volumes"]

    assert live_migration.rollback(args, rpc=object()) == {"status": "ok"}
    assert not fakes.instance_dir.exists()


def test_the_handlers_are_registered_and_pass_the_rpc_client(monkeypatch):
    seen = []
    monkeypatch.setattr(main.live_migration, "prepare", lambda args, rpc: seen.append(("prepare", rpc)) or {"disk_paths": {}})
    monkeypatch.setattr(main.live_migration, "finish", lambda args: seen.append(("finish", None)) or {"status": "ok"})
    monkeypatch.setattr(main.live_migration, "rollback", lambda args, rpc: seen.append(("rollback", rpc)) or {"status": "ok"})

    for method in ("live_migrate_prepare", "live_migrate_finish", "live_migrate_rollback"):
        main.HANDLERS[method]({"id": SERVER})

    assert seen == [("prepare", main._rpc), ("finish", None), ("rollback", main._rpc)]


# --- final-review fixes: instance_dir validation (M1), duplicate initiator names (I5) ------------------------------

@pytest.fixture
def outside(tmp_path):
    # never real system paths: a bug here would rmtree them
    d = tmp_path / "outside"
    d.mkdir()
    (d / "keep").write_text("x")
    return d


def _bad_dirs(fakes, outside):
    return [str(fakes.instance_dir / ".." / ".." / "outside"), str(outside), str(fakes.instance_dir.parent)]


def test_rollback_never_removes_a_dir_outside_the_instances_dir(fakes, outside):
    fakes.instance_dir.mkdir(parents=True)
    for bad in _bad_dirs(fakes, outside):
        args = _args(fakes)
        args["instance_dir"] = bad

        assert live_migration.rollback(args, rpc=object()) == {"status": "ok"}

        assert (outside / "keep").exists() and fakes.instance_dir.parent.exists(), bad


def test_prepare_refuses_a_dir_outside_the_instances_dir(fakes, outside):
    for bad in _bad_dirs(fakes, outside):
        args = _args(fakes)
        args["instance_dir"] = bad

        with pytest.raises(ValueError, match="instance_dir"):
            live_migration.prepare(args, rpc=object())

    assert fakes.of("create_tap") == [] and fakes.of("connect") == []
    assert (outside / "keep").exists()


def test_a_dir_inside_the_instances_dir_is_accepted(fakes):
    assert live_migration._instance_dir({"instance_dir": str(fakes.instance_dir)}) == fakes.instance_dir.resolve()
    assert live_migration._instance_dir({"id": SERVER}) == fakes.instance_dir.resolve()


def test_prepare_refuses_when_both_nodes_share_an_initiator_name_and_a_volume_needs_iscsi(fakes):
    args = {**_args(fakes), "_source_initiator_iqn": fakes.own_iqn}

    with pytest.raises(RuntimeError, match="initiator name"):
        live_migration.prepare(args, rpc=object())

    assert fakes.of("create_tap") == [] and fakes.of("connect") == []
    assert not fakes.instance_dir.exists()


def test_a_shared_initiator_name_is_fine_without_iscsi_volumes(fakes):
    volumes = [{"volume_id": "v-root", "host": None, "path": RBD, "backend": "ceph", "target_dev": "sda"},
               {"volume_id": "v-local", "host": "node-b", "path": "/dev/vg/v-local", "backend": "lvm", "target_dev": "sdb"}]
    args = {**_args(fakes, volumes), "_source_initiator_iqn": fakes.own_iqn}

    assert live_migration.prepare(args, rpc=object())["disk_paths"] == {"v-root": RBD, "v-local": "/dev/vg/v-local"}


@pytest.mark.parametrize("source_iqn", ["iqn.2004-10.com.ubuntu:01:node-a", None])
def test_distinct_or_unknown_initiator_names_are_accepted(fakes, source_iqn):
    args = {**_args(fakes), "_source_initiator_iqn": source_iqn}

    assert "v-remote" in live_migration.prepare(args, rpc=object())["disk_paths"]
