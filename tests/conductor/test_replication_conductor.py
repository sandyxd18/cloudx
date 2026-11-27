from __future__ import annotations

from services.conductor.app import main as conductor_main


def test_handle_update_volume_replication_fields(monkeypatch, tmp_path):
    db_path = tmp_path / "conductor.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

    conductor_main.engine.dispose()
    conductor_main.engine = conductor_main.create_engine(f"sqlite:///{db_path}", pool_pre_ping=True)
    conductor_main.SessionLocal = conductor_main.sessionmaker(bind=conductor_main.engine)

    with conductor_main.SessionLocal() as db:
        db.execute(
            conductor_main.text(
                "CREATE TABLE IF NOT EXISTS block_store_volumes (id VARCHAR PRIMARY KEY, name VARCHAR, project_id VARCHAR, size_gb INTEGER, status VARCHAR, backend VARCHAR, host VARCHAR, replication_status VARCHAR, last_replicated_at TIMESTAMP, error_message VARCHAR)"
            )
        )
        db.execute(
            conductor_main.text(
                "INSERT INTO block_store_volumes (id, name, project_id, size_gb, status, backend, host) VALUES ('vol-test-1', 'vol-test-1', 'proj-1', 10, 'available', 'lvm', 'node-1')"
            )
        )
        db.commit()

    monkeypatch.setattr(conductor_main, "_publish_volume_event", lambda *a, **k: None)
    monkeypatch.setattr(conductor_main, "_row", lambda *a, **k: {"project_id": "proj-1", "status": "available"})

    res = conductor_main.handle_update_volume({
        "volume_id": "vol-test-1",
        "fields": {
            "replication_status": "synced",
            "last_replicated_at": "2026-10-11T12:34:56Z",
            "host": "node-2",
        },
    })

    assert res["ok"] is True
    assert set(res["updated"]) == {"replication_status", "last_replicated_at", "host"}

    with conductor_main.SessionLocal() as db:
        row = db.execute(conductor_main.text("SELECT replication_status, last_replicated_at, host FROM block_store_volumes WHERE id = 'vol-test-1'")).fetchone()
        assert row[0] == "synced"
        assert row[1] is not None
        assert str(row[1]).startswith("2026-10-11 12:34:56")
        assert row[2] == "node-2"


def test_handle_update_volume_garbage_datetime_ignored(monkeypatch, tmp_path):
    db_path = tmp_path / "conductor2.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

    conductor_main.engine.dispose()
    conductor_main.engine = conductor_main.create_engine(f"sqlite:///{db_path}", pool_pre_ping=True)
    conductor_main.SessionLocal = conductor_main.sessionmaker(bind=conductor_main.engine)

    with conductor_main.SessionLocal() as db:
        db.execute(
            conductor_main.text(
                "CREATE TABLE IF NOT EXISTS block_store_volumes (id VARCHAR PRIMARY KEY, name VARCHAR, project_id VARCHAR, size_gb INTEGER, status VARCHAR, backend VARCHAR, host VARCHAR, replication_status VARCHAR, last_replicated_at TIMESTAMP, error_message VARCHAR)"
            )
        )
        db.execute(
            conductor_main.text(
                "INSERT INTO block_store_volumes (id, name, project_id, size_gb, status, backend, host) VALUES ('vol-test-2', 'vol-test-2', 'proj-1', 10, 'available', 'lvm', 'node-1')"
            )
        )
        db.commit()

    monkeypatch.setattr(conductor_main, "_publish_volume_event", lambda *a, **k: None)
    monkeypatch.setattr(conductor_main, "_row", lambda *a, **k: {"project_id": "proj-1", "status": "available"})

    res = conductor_main.handle_update_volume({
        "volume_id": "vol-test-2",
        "fields": {
            "replication_status": "synced",
            "last_replicated_at": "not-a-valid-date",
            "host": "node-3",
        },
    })

    assert res["ok"] is True
    assert set(res["updated"]) == {"replication_status", "host"}

    with conductor_main.SessionLocal() as db:
        row = db.execute(conductor_main.text("SELECT replication_status, last_replicated_at, host FROM block_store_volumes WHERE id = 'vol-test-2'")).fetchone()
        assert row[0] == "synced"
        assert row[1] is None
        assert row[2] == "node-3"


def test_list_all_volumes_carries_created_at_for_the_reconciler(monkeypatch, tmp_path):
    db_path = tmp_path / "conductor.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

    conductor_main.engine.dispose()
    conductor_main.engine = conductor_main.create_engine(f"sqlite:///{db_path}", pool_pre_ping=True)
    conductor_main.SessionLocal = conductor_main.sessionmaker(bind=conductor_main.engine)

    with conductor_main.SessionLocal() as db:
        db.execute(
            conductor_main.text(
                "CREATE TABLE block_store_volumes (id VARCHAR PRIMARY KEY, name VARCHAR, status VARCHAR, backend VARCHAR, "
                "host VARCHAR, path VARCHAR, size_gb INTEGER, attached_to_server_id VARCHAR, target_device VARCHAR, "
                "project_id VARCHAR, created_at TIMESTAMP)"
            )
        )
        db.execute(
            conductor_main.text(
                "INSERT INTO block_store_volumes (id, name, status, backend, size_gb, created_at) "
                "VALUES ('v1', 'v1', 'creating', 'ceph', 1, '2026-11-27 06:25:49')"
            )
        )
        db.commit()

    volumes = conductor_main.handle_list_all_volumes({})["volumes"]

    assert [(v["id"], str(v["created_at"])) for v in volumes] == [("v1", "2026-11-27 06:25:49")]
