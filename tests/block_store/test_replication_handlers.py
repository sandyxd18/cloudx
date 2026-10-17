from __future__ import annotations
import socket

from services.block_store_worker.app import main as worker
from services.block_store_worker.app.replication import ReplicationError


def test_handle_replica_create_success(monkeypatch):
    created = []
    monkeypatch.setattr(worker.lvm, "replica_create", lambda vid, sz: created.append((vid, sz)) or f"/dev/vg/rep-{vid}")
    casts = []
    monkeypatch.setattr(worker, "conductor_update_volume", lambda vid, fields: casts.append((vid, fields)))

    worker.handle_replica_create({"volume_id": "vol-123", "size_gb": 5})

    assert created == [("vol-123", 5)]
    assert len(casts) == 0


def test_handle_replica_create_success_with_sync_from(monkeypatch):
    created = []
    monkeypatch.setattr(worker.lvm, "replica_create", lambda vid, sz: created.append((vid, sz)) or f"/dev/vg/rep-{vid}")
    casts = []
    monkeypatch.setattr(worker, "conductor_update_volume", lambda vid, fields: casts.append((vid, fields)))
    rpc_casts = []
    monkeypatch.setattr(worker._rpc, "cast", lambda queue, action, payload: rpc_casts.append((queue, action, payload)))
    monkeypatch.setattr(worker, "BLOCK_STORE_HOST", "my-host")

    worker.handle_replica_create({"volume_id": "vol-123", "size_gb": 5, "sync_from": "source-node"})

    assert created == [("vol-123", 5)]
    assert len(rpc_casts) == 1
    assert rpc_casts[0][0] == worker.host_queue("block-store", "source-node")
    assert rpc_casts[0][1] == "replica_sync"
    assert rpc_casts[0][2] == {
        "volume_id": "vol-123",
        "size_gb": 5,
        "target_host": "my-host",
    }


def test_handle_replica_create_failure_marks_error(monkeypatch):
    casts = []
    monkeypatch.setattr(worker.lvm, "replica_create", lambda vid, sz: (_ for _ in ()).throw(RuntimeError("fail")))
    monkeypatch.setattr(worker, "conductor_update_volume", lambda vid, fields: casts.append((vid, fields)))

    worker.handle_replica_create({"volume_id": "vol-123", "size_gb": 5})

    assert len(casts) == 1
    assert casts[0][1]["replication_status"] == "error"


def test_handle_replica_create_chained_cast_raising_sets_status_error(monkeypatch):
    created = []
    monkeypatch.setattr(worker.lvm, "replica_create", lambda vid, sz: created.append((vid, sz)) or f"/dev/vg/rep-{vid}")
    casts = []
    monkeypatch.setattr(worker, "conductor_update_volume", lambda vid, fields: casts.append((vid, fields)))
    monkeypatch.setattr(worker._rpc, "cast", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("rabbit down")))
    monkeypatch.setattr(worker, "BLOCK_STORE_HOST", "my-host")

    worker.handle_replica_create({"volume_id": "vol-123", "size_gb": 5, "sync_from": "source-node"})

    assert created == [("vol-123", 5)]
    assert len(casts) == 1
    assert casts[0][1]["replication_status"] == "error"


def test_handle_replica_sync_success(monkeypatch):
    snapshots = []
    deleted = []
    sends = []
    casts = []

    monkeypatch.setattr(worker.lvm, "snapshot", lambda src, snap_name, cow: snapshots.append((src, snap_name, cow)) or f"/dev/vg/{snap_name}")
    monkeypatch.setattr(worker.lvm, "exists", lambda name: True)
    monkeypatch.setattr(worker.lvm, "delete", lambda snap_name: deleted.append(snap_name))
    monkeypatch.setattr(worker.replication, "_lv_size", lambda path: 1048576)
    monkeypatch.setattr(worker, "REPLICATION_PORT", 9870)
    monkeypatch.setattr(worker, "INTERNAL_TOKEN", "secret")
    monkeypatch.setattr(worker, "REPLICATION_PEERS", "target-node=127.0.0.1")
    monkeypatch.setattr(worker.replication, "send", lambda dev, vid, sz, host, port, token: sends.append((dev, vid, sz, host, port, token)) or "sha256abc")
    monkeypatch.setattr(worker, "conductor_update_volume", lambda vid, fields: casts.append((vid, fields)))

    worker.handle_replica_sync({"volume_id": "vol-123", "size_gb": 1, "target_host": "target-node"})

    assert len(snapshots) == 1
    assert snapshots[0][1].startswith("repsnap-")
    assert len(sends) == 1
    assert sends[0][1] == "vol-123"
    assert sends[0][2] == 1048576
    assert sends[0][3] == "127.0.0.1"
    assert sends[0][4] == 9870
    assert sends[0][5] == "secret"
    assert len(deleted) == 1
    assert deleted[0] == snapshots[0][1]
    assert len(casts) == 1
    assert casts[0][0] == "vol-123"
    assert casts[0][1]["replication_status"] == "synced"
    assert "last_replicated_at" in casts[0][1]


def test_handle_replica_sync_replication_error_cleans_snapshot_and_marks_error(monkeypatch):
    deleted = []
    casts = []

    monkeypatch.setattr(worker.lvm, "snapshot", lambda src, snap_name, cow: f"/dev/vg/{snap_name}")
    monkeypatch.setattr(worker.lvm, "exists", lambda name: True)
    monkeypatch.setattr(worker.lvm, "delete", lambda snap_name: deleted.append(snap_name))
    monkeypatch.setattr(worker.replication, "_lv_size", lambda path: 1048576)
    monkeypatch.setattr(worker, "REPLICATION_PORT", 9870)
    monkeypatch.setattr(worker, "INTERNAL_TOKEN", "secret")
    monkeypatch.setattr(worker, "REPLICATION_PEERS", "target-node=127.0.0.1")
    monkeypatch.setattr(worker.replication, "send", lambda *a, **k: (_ for _ in ()).throw(ReplicationError("fail")))
    monkeypatch.setattr(worker, "conductor_update_volume", lambda vid, fields: casts.append((vid, fields)))

    worker.handle_replica_sync({"volume_id": "vol-123", "size_gb": 1, "target_host": "target-node"})

    assert len(deleted) == 1
    assert len(casts) == 1
    assert casts[0][1]["replication_status"] == "error"


def test_handle_replica_sync_snapshot_failure_still_cleans_and_marks_error(monkeypatch):
    deleted = []
    casts = []
    sends_called = []

    monkeypatch.setattr(worker.lvm, "snapshot", lambda src, snap_name, cow: (_ for _ in ()).throw(RuntimeError("snap fail")))
    monkeypatch.setattr(worker.lvm, "exists", lambda name: True)
    monkeypatch.setattr(worker.lvm, "delete", lambda snap_name: deleted.append(snap_name))
    monkeypatch.setattr(worker, "INTERNAL_TOKEN", "secret")
    monkeypatch.setattr(worker, "REPLICATION_PEERS", "target-node=127.0.0.1")
    monkeypatch.setattr(worker.replication, "send", lambda *a, **k: sends_called.append(1))
    monkeypatch.setattr(worker, "conductor_update_volume", lambda vid, fields: casts.append((vid, fields)))

    worker.handle_replica_sync({"volume_id": "vol-123", "size_gb": 1, "target_host": "target-node"})

    assert len(sends_called) == 0
    assert len(casts) == 1
    assert casts[0][1]["replication_status"] == "error"


def test_handle_replica_sync_empty_token_refuses(monkeypatch):
    casts = []
    send_called = []
    snapshot_called = []
    monkeypatch.setattr(worker, "INTERNAL_TOKEN", "")
    monkeypatch.setattr(worker, "REPLICATION_PEERS", "target-node=127.0.0.1")
    monkeypatch.setattr(worker.lvm, "snapshot", lambda *a, **k: snapshot_called.append(1))
    monkeypatch.setattr(worker.replication, "send", lambda *a, **k: send_called.append(1))
    monkeypatch.setattr(worker, "conductor_update_volume", lambda vid, fields: casts.append((vid, fields)))

    worker.handle_replica_sync({"volume_id": "vol-123", "size_gb": 1, "target_host": "target-node"})

    assert len(casts) == 1
    assert casts[0][1]["replication_status"] == "error"
    assert len(send_called) == 0
    assert len(snapshot_called) == 0


def test_handle_replica_sync_unresolvable_target(monkeypatch):
    casts = []
    send_called = []
    snapshot_called = []
    monkeypatch.setattr(worker, "INTERNAL_TOKEN", "secret")
    monkeypatch.setattr(worker, "REPLICATION_PEERS", "")
    monkeypatch.setattr(socket, "gethostbyname", lambda h: (_ for _ in ()).throw(socket.gaierror("not found")))
    monkeypatch.setattr(worker.lvm, "snapshot", lambda *a, **k: snapshot_called.append(1))
    monkeypatch.setattr(worker.replication, "send", lambda *a, **k: send_called.append(1))
    monkeypatch.setattr(worker, "conductor_update_volume", lambda vid, fields: casts.append((vid, fields)))

    worker.handle_replica_sync({"volume_id": "vol-123", "size_gb": 1, "target_host": "bogus"})

    assert len(casts) == 1
    assert casts[0][1]["replication_status"] == "error"
    assert len(send_called) == 0
    assert len(snapshot_called) == 0


def test_handle_replica_promote(monkeypatch):
    promoted = []
    casts = []
    monkeypatch.setattr(worker.lvm, "replica_promote", lambda vid, vname: promoted.append((vid, vname)) or f"/dev/vg/{vname}")
    monkeypatch.setattr(worker, "BLOCK_STORE_HOST", "my-host")
    monkeypatch.setattr(worker, "conductor_update_volume", lambda vid, fields: casts.append((vid, fields)))

    worker.handle_replica_promote({"volume_id": "vol-123", "volume_name": "user-passed-name"})

    assert promoted == [("vol-123", worker._lv_name("vol-123"))]  # ignores volume_name arg and uses _lv_name
    assert len(casts) == 1
    assert casts[0][0] == "vol-123"
    assert casts[0][1]["host"] == "my-host"
    assert "path" in casts[0][1]
    assert casts[0][1]["replication_status"] == "failed-over"


def test_handle_replica_promote_failure(monkeypatch):
    casts = []
    monkeypatch.setattr(worker.lvm, "replica_promote", lambda vid, vname: (_ for _ in ()).throw(RuntimeError("fail")))
    monkeypatch.setattr(worker, "conductor_update_volume", lambda vid, fields: casts.append((vid, fields)))

    worker.handle_replica_promote({"volume_id": "vol-123"})

    assert len(casts) == 1
    assert casts[0][1]["replication_status"] == "error"


def test_handle_replica_delete(monkeypatch):
    deleted = []
    monkeypatch.setattr(worker.lvm, "replica_delete", lambda vid: deleted.append(vid))

    worker.handle_replica_delete({"volume_id": "vol-123"})
    assert deleted == ["vol-123"]


def test_conductor_update_failure_does_not_crash(monkeypatch):
    monkeypatch.setattr(worker.lvm, "snapshot", lambda src, snap_name, cow: f"/dev/vg/{snap_name}")
    monkeypatch.setattr(worker.lvm, "exists", lambda name: True)
    monkeypatch.setattr(worker.lvm, "delete", lambda snap_name: None)
    monkeypatch.setattr(worker.replication, "_lv_size", lambda path: 1048576)
    monkeypatch.setattr(worker, "REPLICATION_PORT", 9870)
    monkeypatch.setattr(worker, "INTERNAL_TOKEN", "secret")
    monkeypatch.setattr(worker, "REPLICATION_PEERS", "target-node=127.0.0.1")
    monkeypatch.setattr(worker.replication, "send", lambda *a, **k: (_ for _ in ()).throw(ReplicationError("send fail")))
    
    def bad_update(*a, **k):
        raise RuntimeError("rpc fail")
    monkeypatch.setattr(worker, "conductor_update_volume", bad_update)

    # Should not raise exception
    worker.handle_replica_sync({"volume_id": "vol-123", "size_gb": 1, "target_host": "target-node"})


def test_allowed_peer_logic_with_malformed_entries(monkeypatch):
    monkeypatch.setattr(worker, "REPLICATION_PEERS", "  node1 = 10.0.0.1 , , bad , x= , node2=10.0.0.2 ")
    allowed = worker._build_allowed_peer()
    peer_map = worker._parse_replication_peers()

    assert peer_map == {"node1": "10.0.0.1", "node2": "10.0.0.2"}
    assert allowed("10.0.0.1") is True
    assert allowed("10.0.0.2") is True
    assert allowed("127.0.0.1") is True
    assert allowed("::1") is True
    assert allowed("10.0.0.3") is False


def test_allowed_peer_empty_config(monkeypatch):
    monkeypatch.setattr(worker, "REPLICATION_PEERS", "")
    allowed = worker._build_allowed_peer()
    peer_map = worker._parse_replication_peers()

    assert peer_map == {}
    assert allowed("127.0.0.1") is True
    assert allowed("::1") is True
    assert allowed("10.0.0.1") is False


def test_receiver_started_and_not_started(monkeypatch):
    served = []
    monkeypatch.setattr(worker.replication, "serve", lambda port, token, peer_fn: served.append((port, token)))
    monkeypatch.setattr(worker, "STORAGE_LVM_DRIVER", True)
    monkeypatch.setattr(worker, "INTERNAL_TOKEN", "secret")
    monkeypatch.setattr(worker, "REPLICATION_PORT", 9999)

    worker._maybe_start_receiver()
    assert served == [(9999, "secret")]

    served.clear()
    monkeypatch.setattr(worker, "INTERNAL_TOKEN", "")
    worker._maybe_start_receiver()
    assert served == []

    served.clear()
    monkeypatch.setattr(worker, "INTERNAL_TOKEN", "secret")
    monkeypatch.setattr(worker, "STORAGE_LVM_DRIVER", False)
    worker._maybe_start_receiver()
    assert served == []
