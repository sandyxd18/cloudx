"""The reconciler looked for a Ceph snapshot as an image called snap-<id>, which never exists, so every Ceph snapshot was
marked `error` within a minute of being made (seen on the lab with 1.0.1~rc1; the worker log had just said it created it)."""
import pytest

from services.block_store_worker.app import reconciler
from services.block_store_worker.app.drivers import ceph

PATH = "rbd:volumes/vol-abc@snap-123"


@pytest.fixture
def rbd(monkeypatch):
    seen = []
    monkeypatch.setattr(ceph, "CEPH_POOL", "volumes")

    def fake(*args, **kwargs):
        seen.append(list(args))
        if fake.error:
            raise RuntimeError(fake.error)
        return ""

    fake.error = None
    monkeypatch.setattr(ceph, "_rbd", fake)
    return seen, fake


def test_a_snapshot_is_looked_up_as_image_at_snap(rbd):
    seen, _ = rbd
    assert ceph.snapshot_exists(PATH) is True
    assert seen == [["info", "volumes/vol-abc@snap-123"]]


def test_a_missing_snapshot_is_false_and_any_other_error_is_raised(rbd):
    _, fake = rbd
    fake.error = "rbd: error opening image vol-abc: (2) No such file or directory"
    assert ceph.snapshot_exists(PATH) is False
    fake.error = "rbd: couldn't connect to the cluster!"
    with pytest.raises(RuntimeError):
        ceph.snapshot_exists(PATH)


def test_the_image_check_is_unchanged(rbd):
    seen, _ = rbd
    assert ceph.exists("vol-abc") is True
    assert seen == [["info", "volumes/vol-abc"]]


def test_the_reconciler_checks_a_ceph_snapshot_by_its_stored_path(monkeypatch):
    asked = []
    monkeypatch.setattr(ceph, "snapshot_exists", lambda path: asked.append(path) or True)
    monkeypatch.setattr(ceph, "exists", lambda name: pytest.fail("a snapshot is not an image called snap-<id>"))
    assert reconciler._backend_exists_for_snapshot({"id": "s-1", "backend": "ceph", "path": PATH}) is True
    assert asked == [PATH]


def test_a_ceph_snapshot_without_a_path_or_with_an_unreadable_cluster_is_unknown(monkeypatch):
    # unknown is never treated as missing
    assert reconciler._backend_exists_for_snapshot({"id": "s-2", "backend": "ceph", "path": ""}) is None

    def boom(path):
        raise RuntimeError("cluster unreachable")

    monkeypatch.setattr(ceph, "snapshot_exists", boom)
    assert reconciler._backend_exists_for_snapshot({"id": "s-3", "backend": "ceph", "path": PATH}) is None


def test_an_existing_ceph_snapshot_is_left_alone_and_one_in_error_recovers(monkeypatch):
    casts = []

    class Rpc:
        def call(self, *a, **k):
            return {"snapshots": [
                {"id": "ok", "backend": "ceph", "status": "available", "path": PATH},
                {"id": "was-flagged", "backend": "ceph", "status": "error", "path": PATH},
                {"id": "gone", "backend": "ceph", "status": "available", "path": "rbd:volumes/vol-x@snap-x"},
            ]}

        def cast(self, target, method, payload):
            casts.append((method, payload["snapshot_id"]))

    monkeypatch.setattr(reconciler, "_owned_by_other_node", lambda snap: False)
    monkeypatch.setattr(ceph, "snapshot_exists", lambda path: path == PATH)
    reconciler.reconcile_snapshots(Rpc())
    assert casts == [("update_snapshot", "was-flagged"), ("mark_snapshot_error", "gone")]
