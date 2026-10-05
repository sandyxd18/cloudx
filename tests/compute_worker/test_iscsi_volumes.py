from pathlib import Path

import pytest
from services.block_store_worker.app import main as block_worker
from services.compute_worker.app import iscsi, volume_manager

VOLUME_ID = "11111111-2222-3333-4444-555555555555"
LV = "vol-111111112222333344445555"
IQN = f"iqn.2026-10.io.cloudx:{LV}"
INITIATOR = "iqn.2004-10.com.ubuntu:01:compute"
PORTAL = "192.168.10.12:3260"


class FakeRpc:
    def __init__(self, reply=None):
        self.reply = reply if reply is not None else {"target_iqn": IQN, "portal": PORTAL, "lun": 0}
        self.calls = []

    def call(self, routing_key, method, args, timeout=None):
        self.calls.append((routing_key, method, args))
        return self.reply


@pytest.fixture(autouse=True)
def this_node(monkeypatch):
    monkeypatch.setattr(volume_manager, "COMPUTE_HOST", "node-a")
    monkeypatch.setattr(iscsi, "initiator_iqn", lambda: INITIATOR)


def test_lv_name_matches_the_name_the_storage_node_gives_the_lv():
    assert iscsi.lv_name(VOLUME_ID) == block_worker._lv_name(VOLUME_ID) == LV


def test_initiator_name_is_read_from_the_open_iscsi_file(monkeypatch, tmp_path):
    monkeypatch.undo()
    file = tmp_path / "initiatorname.iscsi"
    file.write_text("## comment\nInitiatorName=iqn.1993-08.org.debian:01:abc\n")
    monkeypatch.setattr(iscsi, "INITIATOR_FILE", file)
    assert iscsi.initiator_iqn() == "iqn.1993-08.org.debian:01:abc"
    file.write_text("# nothing here\n")
    with pytest.raises(RuntimeError, match="open-iscsi"):
        iscsi.initiator_iqn()


# ── connect ───────────────────────────────────────────────────────────────────

def test_a_local_volume_is_used_through_its_own_path_without_any_rpc():
    rpc = FakeRpc()
    volume = {"volume_id": VOLUME_ID, "path": "/dev/vg-cloudx/" + LV, "host": "node-a"}
    assert volume_manager.connect(volume, rpc) == Path("/dev/vg-cloudx/" + LV)
    assert rpc.calls == []


def test_a_volume_without_a_host_is_local():
    assert volume_manager.connect({"volume_id": VOLUME_ID, "path": "/x.qcow2", "host": None}, FakeRpc()) == Path("/x.qcow2")


def test_a_volume_on_another_node_is_exported_and_logged_in(monkeypatch):
    rpc = FakeRpc()
    logins = []
    monkeypatch.setattr(iscsi, "login", lambda portal, iqn, lun=0: logins.append((portal, iqn, lun)) or Path("/dev/sdx"))

    path = volume_manager.connect({"volume_id": VOLUME_ID, "path": "/dev/vg-cloudx/" + LV, "host": "node-b"}, rpc)

    assert path == Path("/dev/sdx")
    assert rpc.calls == [("block-store.node-b", "export_volume", {"volume_id": VOLUME_ID, "initiator_iqn": INITIATOR})]
    assert logins == [(PORTAL, IQN, 0)]


def test_the_root_volume_dict_from_block_store_uses_id_not_volume_id(monkeypatch):
    rpc = FakeRpc()
    monkeypatch.setattr(iscsi, "login", lambda *a, **k: Path("/dev/sdx"))
    volume_manager.connect({"id": VOLUME_ID, "path": "", "host": "node-b"}, rpc)
    assert rpc.calls[0][2]["volume_id"] == VOLUME_ID


def test_a_refused_export_is_reported_with_the_storage_nodes_reason(monkeypatch):
    monkeypatch.setattr(iscsi, "login", lambda *a, **k: pytest.fail("must not log in after a refusal"))
    rpc = FakeRpc({"error": "iSCSI export is not enabled on this storage node (ISCSI_EXPORT=true)"})
    with pytest.raises(RuntimeError, match="node-b refused.*ISCSI_EXPORT"):
        volume_manager.connect({"volume_id": VOLUME_ID, "path": "/p", "host": "node-b"}, rpc)


# ── attach / detach ───────────────────────────────────────────────────────────

def attach_payload(host):
    return {"libvirt_domain_name": "dom", "volume": {"volume_id": VOLUME_ID, "path": "/dev/vg-cloudx/" + LV, "host": host, "target_dev": "sdb"}}


def test_attaching_a_remote_volume_hands_libvirt_the_iscsi_device(monkeypatch, tmp_path):
    device = tmp_path / "by-path-device"
    device.write_bytes(b"")
    attached = []
    monkeypatch.setattr(iscsi, "login", lambda *a, **k: device)
    monkeypatch.setattr(volume_manager.libvirt_driver, "attach_disk", lambda dom, path, target, fmt: attached.append((dom, path, target)))

    result = volume_manager.attach_volume(attach_payload("node-b"), FakeRpc())

    assert attached == [("dom", device, "sdb")] and result["path"] == str(device)


def test_a_failed_attach_does_not_leave_the_session_behind(monkeypatch, tmp_path):
    device = tmp_path / "dev"
    device.write_bytes(b"")
    rpc = FakeRpc()
    logouts = []
    monkeypatch.setattr(iscsi, "login", lambda *a, **k: device)
    monkeypatch.setattr(iscsi, "find_session", lambda vid: (PORTAL, IQN))
    monkeypatch.setattr(iscsi, "logout", lambda portal, iqn: logouts.append((portal, iqn)))

    def boom(*a):
        raise RuntimeError("libvirt said no")

    monkeypatch.setattr(volume_manager.libvirt_driver, "attach_disk", boom)

    with pytest.raises(RuntimeError, match="libvirt said no"):
        volume_manager.attach_volume(attach_payload("node-b"), rpc)

    assert logouts == [(PORTAL, IQN)]
    assert [c[1] for c in rpc.calls] == ["export_volume", "unexport_volume"]


def test_attaching_a_local_volume_needs_no_iscsi(monkeypatch, tmp_path):
    local = tmp_path / "lv"
    local.write_bytes(b"")
    payload = attach_payload("node-a")
    payload["volume"]["path"] = str(local)
    attached = []
    monkeypatch.setattr(volume_manager.libvirt_driver, "attach_disk", lambda dom, path, target, fmt: attached.append(path))
    monkeypatch.setattr(iscsi, "login", lambda *a, **k: pytest.fail("local volumes are not logged in"))
    volume_manager.attach_volume(payload, FakeRpc())
    assert attached == [local]


def test_detaching_a_remote_volume_uses_the_session_device_then_logs_out_and_unexports(monkeypatch):
    rpc = FakeRpc({})
    steps = []
    monkeypatch.setattr(iscsi, "find_session", lambda vid: (PORTAL, IQN))
    monkeypatch.setattr(iscsi, "logout", lambda portal, iqn: steps.append(("logout", portal, iqn)))
    monkeypatch.setattr(volume_manager.libvirt_driver, "detach_disk", lambda dom, path, target, fmt: steps.append(("detach", str(path), target)))

    volume_manager.detach_volume(attach_payload("node-b"), rpc)

    assert steps == [("detach", str(iscsi.device_path(PORTAL, IQN)), "sdb"), ("logout", PORTAL, IQN)]
    assert rpc.calls == [("block-store.node-b", "unexport_volume", {"volume_id": VOLUME_ID, "initiator_iqn": INITIATOR})]


def test_disconnect_of_a_local_volume_does_nothing(monkeypatch):
    rpc = FakeRpc()
    monkeypatch.setattr(iscsi, "find_session", lambda vid: None)
    volume_manager.disconnect(VOLUME_ID, "node-a", rpc)
    volume_manager.disconnect(VOLUME_ID, None, rpc)
    assert rpc.calls == []


# ── session lookup ────────────────────────────────────────────────────────────

def test_find_session_matches_the_volumes_target(monkeypatch):
    listing = (
        f"tcp: [1] 192.168.10.12:3260,1 iqn.2026-10.io.cloudx:vol-other (non-flash)\n"
        f"tcp: [2] 192.168.10.12:3260,1 {IQN} (non-flash)\n"
    )

    class Result:
        stdout = listing

    monkeypatch.setattr(iscsi, "run", lambda cmd, **k: Result())
    assert iscsi.find_session(VOLUME_ID) == (PORTAL, IQN)
    assert iscsi.find_session("99999999-0000-0000-0000-000000000000") is None


def test_login_tolerates_an_existing_record_and_waits_for_the_device(monkeypatch, tmp_path):
    device = tmp_path / "ip-x-iscsi-t-lun-0"
    commands = []

    class Result:
        def __init__(self, rc):
            self.returncode = rc

    def fake_run(cmd, **k):
        commands.append(cmd[-1])
        device.write_bytes(b"") if cmd[-1] == "--login" else None
        return Result(15 if cmd[-1] == "new" else 0)  # 15 = record already exists

    monkeypatch.setattr(iscsi, "run", fake_run)
    monkeypatch.setattr(iscsi, "device_path", lambda portal, iqn, lun=0: device)
    assert iscsi.login(PORTAL, IQN) == device
    assert commands == ["new", "--login"]
