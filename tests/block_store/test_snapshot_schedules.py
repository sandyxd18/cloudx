from fastapi.testclient import TestClient

from tests.stack import fresh_service


def test_block_store_scheduled_snapshots(monkeypatch, tmp_path):
    db_path = tmp_path / "block_schedule.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    bs_main = fresh_service("block_store", monkeypatch, tmp_path)

    from shared.auth_middleware import require_auth
    bs_main.app.dependency_overrides[require_auth] = lambda: {"project_id": "proj-1", "role": "admin"}
    client = TestClient(bs_main.app)

    # Seed Volume
    with bs_main.SessionLocal() as db:
        vol = bs_main.Volume(id="vol-sch-1", name="data-vol", project_id="proj-1", size_gb=10, status="available")
        db.add(vol)
        db.commit()

    # Create schedule
    res_post = client.post(
        "/volumes/vol-sch-1/schedules",
        json={"interval_seconds": 3600, "retention_count": 2},
    )
    assert res_post.status_code == 201
    sch_data = res_post.json()
    assert sch_data["volume_id"] == "vol-sch-1"
    assert sch_data["retention_count"] == 2

    # Execute schedule runner logic manually
    bs_main._run_snapshot_schedules()

    # Verify snapshot created
    with bs_main.SessionLocal() as db:
        snaps = db.query(bs_main.Snapshot).filter_by(volume_id="vol-sch-1").all()
        assert len(snaps) == 1

    # Execute again to trigger retention limit
    with bs_main.SessionLocal() as db:
        sch = db.get(bs_main.VolumeSnapshotSchedule, sch_data["id"])
        sch.last_run_at = None
        db.commit()

    bs_main._run_snapshot_schedules()

    with bs_main.SessionLocal() as db:
        sch = db.get(bs_main.VolumeSnapshotSchedule, sch_data["id"])
        sch.last_run_at = None
        db.commit()

    bs_main._run_snapshot_schedules()

    with bs_main.SessionLocal() as db:
        snaps = db.query(bs_main.Snapshot).filter_by(volume_id="vol-sch-1").all()
        assert len(snaps) == 2  # capped by retention_count=2
