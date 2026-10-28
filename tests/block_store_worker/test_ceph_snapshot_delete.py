import pytest

from services.block_store_worker.app import main as w
from services.block_store_worker.app.drivers import ceph


@pytest.fixture
def rbd(monkeypatch):
    seen = []
    monkeypatch.setattr(ceph, "CEPH_POOL", "volumes")
    monkeypatch.setattr(ceph, "_rbd", lambda *a, **k: seen.append(list(a)) or "")
    return seen


def test_deleting_a_ceph_snapshot_removes_the_rbd_snapshot(rbd):
    # a Ceph snapshot is pool/image@snap; the worker used to run `rbd rm pool/snap-xxx`, which removes nothing
    ceph.delete_snapshot("rbd:volumes/vol-abc@snap-123")

    assert rbd == [["snap", "rm", "volumes/vol-abc@snap-123"]]


def test_the_worker_deletes_a_ceph_snapshot_through_its_stored_path(rbd, monkeypatch):
    dropped = []
    monkeypatch.setattr(w, "conductor_delete_snapshot", lambda snapshot_id: dropped.append(snapshot_id))

    w.handle_delete_snapshot({"snapshot_id": "s-1", "backend": "ceph", "path": "rbd:volumes/vol-abc@snap-s1"})

    assert rbd == [["snap", "rm", "volumes/vol-abc@snap-s1"]]
    assert dropped == ["s-1"]


def test_a_ceph_snapshot_that_never_got_a_path_has_nothing_to_remove(rbd, monkeypatch):
    dropped = []
    monkeypatch.setattr(w, "conductor_delete_snapshot", lambda snapshot_id: dropped.append(snapshot_id))

    w.handle_delete_snapshot({"snapshot_id": "s-2", "backend": "ceph", "path": ""})

    assert rbd == [] and dropped == ["s-2"]
