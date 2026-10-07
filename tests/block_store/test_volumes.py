from pathlib import Path

from tests.stack import fresh_service


def test_block_store_detach_server_volumes(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "block.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("VOLUME_STORE_PATH", str(tmp_path / "volumes"))
    block_main = fresh_service("block_store", monkeypatch, tmp_path)
    db = block_main.SessionLocal()
    volume = block_main.Volume(
        id="vol-id",
        name="vol1",
        size_gb=1,
        status="in-use",
        attached_to_server_id="server-id",
        path=str(tmp_path / "volumes" / "vol-id.qcow2"),
    )
    db.add(volume)
    db.commit()

    result = block_main.detach_server_volumes("server-id", db)
    assert len(result["detached"]) == 1
    refreshed = block_main.require_volume(db, "vol1")
    assert refreshed.status == "available"
    assert refreshed.attached_to_server_id is None
    assert refreshed.target_device is None

    refreshed.status = "in-use"
    refreshed.attached_to_server_id = "deleted-server-id"
    db.commit()
    forced = block_main.force_detach_volume("vol1", {"role": "admin"}, db)
    assert forced["status"] == "available"
    assert forced["attached_to_server_id"] is None
    db.close()

def test_block_store_snapshot_and_volume_from_snapshot(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "block-snap.sqlite"
    volume_root = tmp_path / "volumes"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("STORAGE_LVM_DRIVER", "true")
    block_main = fresh_service("block_store", monkeypatch, tmp_path)
    monkeypatch.setattr(block_main.clients, "cast_to_worker", lambda method, payload: None)
    db = block_main.SessionLocal()
    volume_path = volume_root / "vol-id.qcow2"
    volume_path.parent.mkdir(parents=True, exist_ok=True)
    volume_path.write_bytes(b"volume-bytes")
    vt = block_main.VolumeType(id="vt-default", name="default", backend="lvm", is_public=True)
    volume = block_main.Volume(id="vol-id", name="vol1", size_gb=1, status="available", path=str(volume_path), bootable=True)
    db.add_all([vt, volume])
    db.commit()

    dummy_user = {"project_id": "proj-1", "role": "admin"}
    snap = block_main.create_volume_snapshot("vol1", block_main.SnapshotIn(name="snap1"), dummy_user, db)
    assert snap["name"] == "snap1"
    s = db.get(block_main.Snapshot, snap["id"])
    s.status, s.path = "available", str(volume_path)
    db.commit()

    created = block_main.create_volume(block_main.VolumeIn(name="vol2", size_gb=1, snapshot="snap1"), dummy_user, db)
    assert created["snapshot_id"] == snap["id"]
    assert created["source_type"] == "snapshot"
    db.close()
