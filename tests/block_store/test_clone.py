import pytest
from fastapi import HTTPException
from tests.stack import fresh_service

USER = {"project_id": "proj-1", "role": "admin"}


@pytest.fixture
def block(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_LVM_DRIVER", "true")
    svc = fresh_service("block_store", monkeypatch, tmp_path)
    casts = []

    class FakeRpc:
        def cast(self, routing_key, method, payload):
            casts.append((method, payload))

        def publish_event(self, *a, **k):
            pass

    monkeypatch.setattr(svc.clients, "_rpc", FakeRpc())
    db = svc.SessionLocal()
    db.add(svc.VolumeType(id="vt", name="default", backend="lvm", is_public=True))
    db.add(svc.Volume(id="v1", name="src", size_gb=5, status="available", path="/dev/vg/src", backend="lvm", project_id="proj-1"))
    db.add(svc.Snapshot(id="s1", name="snap", volume_id="v1", size_gb=5, status="available", path="/dev/vg/snap", backend="lvm"))
    db.add(svc.Snapshot(id="s2", name="pending", volume_id="v1", size_gb=5, status="creating", path="", backend="lvm"))
    db.commit()
    yield svc, db, casts
    db.close()


def test_clone_from_snapshot_sends_the_snapshot_path(block):
    svc, db, casts = block
    svc.create_volume(svc.VolumeIn(name="c1", size_gb=5, snapshot="snap"), USER, db)
    assert casts[-1][0] == "create_volume" and casts[-1][1]["snapshot_path"] == "/dev/vg/snap"


def test_clone_from_volume_sends_the_source_path(block):
    svc, db, casts = block
    svc.create_volume(svc.VolumeIn(name="c2", size_gb=5, source_volume="src"), USER, db)
    assert casts[-1][1]["source_path"] == "/dev/vg/src"


def test_clone_rejects_unready_or_too_small_sources(block):
    svc, db, casts = block
    for kw, code in (({"snapshot": "pending"}, 409), ({"snapshot": "snap", "size_gb": 1}, 400),
                     ({"source_volume": "src", "size_gb": 1}, 400)):
        with pytest.raises(HTTPException) as e:
            svc.create_volume(svc.VolumeIn(**{"name": "x", "size_gb": 5, **kw}), USER, db)
        assert e.value.status_code == code
    assert casts == []
