import pytest

from services.block_store_worker.app import main as w


@pytest.fixture
def calls(monkeypatch):
    c = []
    monkeypatch.setattr(w, "_mc_alias", lambda: None)
    monkeypatch.setattr(w, "_mc", lambda *a, **k: c.append(("upload",)))
    monkeypatch.setattr(w, "conductor_update_backup", lambda *a: c.append(("ok",)))
    monkeypatch.setattr(w, "conductor_mark_backup_error", lambda *a: c.append(("err",)))
    monkeypatch.setattr(w.lvm, "snapshot", lambda lv, snap, gb: c.append(("snap", lv, gb)) or f"/dev/vg/{snap}")
    monkeypatch.setattr(w.lvm, "delete", lambda name: c.append(("del", name)))
    monkeypatch.setattr(w, "_export_volume_to_file", lambda path, backend, dest: c.append(("export", path)))
    return c


ARGS = {"backup_id": "bk-1", "volume_path": "/dev/vg/vol-abc", "backend": "lvm", "size_gb": 10, "object_key": "k"}


def test_lvm_backup_exports_a_snapshot_and_removes_it(calls):
    w.handle_backup_volume(ARGS)
    names = [x[0] for x in calls]
    assert names == ["snap", "export", "upload", "ok", "del"]
    snap = next(x for x in calls if x[0] == "snap")
    assert snap[1] == "vol-abc" and snap[2] == 2  # 20% copy-on-write space
    assert next(x for x in calls if x[0] == "export")[1].startswith("/dev/vg/snap-")
    assert next(x for x in calls if x[0] == "del")[1].startswith("snap-")


def test_snapshot_is_removed_when_the_export_fails(calls, monkeypatch):
    def boom(*a):
        raise RuntimeError("disk")
    monkeypatch.setattr(w, "_export_volume_to_file", boom)
    w.handle_backup_volume(ARGS)
    assert ("err",) in calls and any(x[0] == "del" for x in calls)


def test_ceph_backup_keeps_the_old_behaviour(calls):
    w.handle_backup_volume({**ARGS, "backend": "ceph", "volume_path": "rbd:p/img"})
    assert not any(x[0] in ("snap", "del") for x in calls)
