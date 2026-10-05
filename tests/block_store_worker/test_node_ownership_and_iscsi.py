import pytest
from services.block_store_worker.app import main as worker
from services.block_store_worker.app import reconciler
from services.block_store_worker.app.drivers import iscsi
from shared.proc import CommandError


# ── reconciler: an LV belongs to one node ─────────────────────────────────────

class FakeRpc:
    def __init__(self, volumes=(), snapshots=()):
        self.volumes, self.snapshots, self.casts = list(volumes), list(snapshots), []

    def call(self, routing_key, method, args, timeout=None):
        return {"volumes": self.volumes} if method == "list_all_volumes" else {"snapshots": self.snapshots}

    def cast(self, routing_key, method, args):
        self.casts.append((method, args))


def volume(vol_id, host, status="available", backend="lvm"):
    return {"id": vol_id, "name": vol_id, "status": status, "backend": backend, "host": host, "path": ""}


def test_reconciler_never_judges_another_nodes_lv(monkeypatch):
    monkeypatch.setattr(reconciler, "BLOCK_STORE_HOST", "node-a")
    checked = []
    monkeypatch.setattr(reconciler.lvm, "exists", lambda name: checked.append(name) or False)  # nothing exists locally
    rpc = FakeRpc([volume("mine", "node-a"), volume("theirs", "node-b"), volume("legacy", None)])

    reconciler.reconcile_volumes(rpc)

    flagged = {args["volume_id"] for method, args in rpc.casts if method == "mark_volume_error"}
    assert flagged == {"mine", "legacy"}  # node-b's LV is not ours to mark missing
    assert len(checked) == 2


def test_reconciler_still_checks_non_lvm_volumes_whatever_their_host(monkeypatch):
    monkeypatch.setattr(reconciler, "BLOCK_STORE_HOST", "node-a")
    rpc = FakeRpc([{**volume("nfs-vol", "node-b", backend="nfs"), "path": "/does/not/exist.qcow2"}])
    reconciler.reconcile_volumes(rpc)
    assert [args["volume_id"] for method, args in rpc.casts] == ["nfs-vol"]


def test_reconciler_skips_snapshots_of_another_node(monkeypatch):
    monkeypatch.setattr(reconciler, "BLOCK_STORE_HOST", "node-a")
    monkeypatch.setattr(reconciler.lvm, "exists", lambda name: False)
    snaps = [{"id": "s-mine", "status": "available", "backend": "lvm", "host": "node-a"},
             {"id": "s-theirs", "status": "available", "backend": "lvm", "host": "node-b"}]
    rpc = FakeRpc(snapshots=snaps)

    reconciler.reconcile_snapshots(rpc)

    assert [args["snapshot_id"] for method, args in rpc.casts if method == "mark_snapshot_error"] == ["s-mine"]


# ── iSCSI export: targetcli command sequence ──────────────────────────────────

IQN = "iqn.2026-10.io.cloudx:vol-abc"
INITIATOR = "iqn.2004-10.com.ubuntu:01:compute"


class FakeTargetcli:
    """Keeps the set of existing targetcli paths: `ls` probes read it, create/delete commands change it."""

    def __init__(self, existing=(), acls=1):
        self.existing, self.commands, self.acls = set(existing), [], acls

    def install(self, monkeypatch):
        monkeypatch.setattr(iscsi, "succeeds", lambda cmd, **k: cmd[1] in self.existing and cmd[2] == "ls")
        monkeypatch.setattr(iscsi, "run", self.run)
        monkeypatch.setattr(iscsi, "output", lambda cmd, **k: f"o- acls ........ [ACLs: {self.acls}]")
        monkeypatch.setenv("ISCSI_PORTAL_IP", "192.168.10.12")

    def run(self, cmd, **k):
        self.commands.append(" ".join(cmd[1:]))
        path, verb, *rest = (cmd[1:] + ["", ""])[:4]
        if verb == "delete":
            target = rest[0]
            self.existing.discard(f"{path}/{target}")
            self.existing.discard(target if path == "/iscsi" else f"{path}/{target}")
            if path.endswith("/acls"):
                self.acls -= 1


def test_export_builds_backstore_target_lun_and_acl_and_reports_the_portal(monkeypatch):
    fake = FakeTargetcli()
    fake.install(monkeypatch)

    info = iscsi.export("vol-abc", "/dev/vg-cloudx/vol-abc", INITIATOR)

    assert fake.commands == [
        "/backstores/block create name=vol-abc dev=/dev/vg-cloudx/vol-abc",
        f"/iscsi create {IQN}",
        f"/iscsi/{IQN}/tpg1/luns create /backstores/block/vol-abc lun=0",
        f"/iscsi/{IQN}/tpg1/acls create {INITIATOR}",
        "saveconfig",
    ]
    assert info == {"target_iqn": IQN, "portal": "192.168.10.12:3260", "lun": 0}


def test_export_is_idempotent(monkeypatch):
    fake = FakeTargetcli(existing={"/backstores/block/vol-abc", f"/iscsi/{IQN}", f"/iscsi/{IQN}/tpg1/luns/lun0",
                                   f"/iscsi/{IQN}/tpg1/acls/{INITIATOR}"})
    fake.install(monkeypatch)
    iscsi.export("vol-abc", "/dev/x", INITIATOR)
    assert fake.commands == ["saveconfig"]


def test_unexport_keeps_the_target_while_another_initiator_may_still_use_it(monkeypatch):
    fake = FakeTargetcli(existing={"/backstores/block/vol-abc", f"/iscsi/{IQN}", f"/iscsi/{IQN}/tpg1/acls/{INITIATOR}"}, acls=2)
    fake.install(monkeypatch)
    iscsi.unexport("vol-abc", INITIATOR)
    assert fake.commands == [f"/iscsi/{IQN}/tpg1/acls delete {INITIATOR}", "saveconfig"]


def test_unexport_removes_target_and_backstore_with_the_last_initiator(monkeypatch):
    fake = FakeTargetcli(existing={"/backstores/block/vol-abc", f"/iscsi/{IQN}", f"/iscsi/{IQN}/tpg1/acls/{INITIATOR}"}, acls=1)
    fake.install(monkeypatch)
    iscsi.unexport("vol-abc", INITIATOR)
    assert fake.commands == [f"/iscsi/{IQN}/tpg1/acls delete {INITIATOR}", f"/iscsi delete {IQN}",
                             "/backstores/block delete vol-abc", "saveconfig"]


def test_unexport_of_something_never_exported_only_saves(monkeypatch):
    fake = FakeTargetcli()
    fake.install(monkeypatch)
    iscsi.unexport("vol-abc", INITIATOR)
    assert fake.commands == ["saveconfig"]


def test_portal_must_not_be_loopback(monkeypatch):
    monkeypatch.delenv("ISCSI_PORTAL_IP", raising=False)
    monkeypatch.setattr(iscsi.socket, "gethostbyname", lambda name: "127.0.1.1")
    with pytest.raises(RuntimeError, match="ISCSI_PORTAL_IP"):
        iscsi.portal_ip()


def test_missing_targetcli_is_an_error_not_a_silent_no(monkeypatch):
    def probe_cannot_run(cmd, **k):
        raise CommandError(cmd, 127)

    monkeypatch.setattr(iscsi, "succeeds", probe_cannot_run)
    with pytest.raises(CommandError, match="command not found"):
        iscsi.export("vol-abc", "/dev/x", INITIATOR)


# ── handlers ──────────────────────────────────────────────────────────────────

def test_export_handler_refuses_when_the_node_does_not_export(monkeypatch):
    monkeypatch.setattr(worker, "ISCSI_EXPORT", False)
    with pytest.raises(RuntimeError, match="ISCSI_EXPORT"):
        worker.handle_export_volume({"volume_id": "abc", "initiator_iqn": INITIATOR})


def test_export_handler_exports_the_volumes_lv(monkeypatch):
    seen = {}
    monkeypatch.setattr(worker, "ISCSI_EXPORT", True)
    monkeypatch.setattr(worker.lvm, "device_path", lambda lv: f"/dev/vg/{lv}")
    monkeypatch.setattr(worker.iscsi, "export", lambda lv, dev, iqn: seen.update(lv=lv, dev=dev, iqn=iqn) or {"target_iqn": "t"})
    assert worker.handle_export_volume({"volume_id": "11111111-2222-3333-4444-555555555555", "initiator_iqn": INITIATOR}) == {"target_iqn": "t"}
    assert seen == {"lv": "vol-111111112222333344445555", "dev": "/dev/vg/vol-111111112222333344445555", "iqn": INITIATOR}


def test_delete_drops_the_iscsi_target_before_the_lv(monkeypatch):
    order = []
    monkeypatch.setattr(worker, "ISCSI_EXPORT", True)
    monkeypatch.setattr(worker.iscsi, "remove", lambda lv: order.append("remove target"))
    monkeypatch.setattr(worker.lvm, "delete", lambda lv: order.append("lvremove"))
    monkeypatch.setattr(worker, "conductor_delete_volume", lambda vid: order.append("db record"))
    worker.handle_delete_volume({"volume_id": "abc", "backend": "lvm"})
    assert order == ["remove target", "lvremove", "db record"]


def test_delete_without_iscsi_export_never_calls_targetcli(monkeypatch):
    monkeypatch.setattr(worker, "ISCSI_EXPORT", False)
    monkeypatch.setattr(worker.iscsi, "remove", lambda lv: pytest.fail("targetcli must not run when export is off"))
    monkeypatch.setattr(worker.lvm, "delete", lambda lv: None)
    monkeypatch.setattr(worker, "conductor_delete_volume", lambda vid: None)
    worker.handle_delete_volume({"volume_id": "abc", "backend": "lvm"})
