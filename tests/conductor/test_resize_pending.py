from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from services.conductor.app import main as conductor_main

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "services/compute/migrations/versions/compute_0005_resize_pending_since.py"
)


def _setup(monkeypatch, tmp_path, status="ACTIVE"):
    db_path = tmp_path / "conductor.sqlite"
    conductor_main.engine.dispose()
    conductor_main.engine = conductor_main.create_engine(f"sqlite:///{db_path}")
    conductor_main.SessionLocal = conductor_main.sessionmaker(bind=conductor_main.engine)
    with conductor_main.SessionLocal() as db:
        db.execute(conductor_main.text(
            "CREATE TABLE compute_servers (id VARCHAR PRIMARY KEY, project_id VARCHAR, status VARCHAR, "
            "error_message VARCHAR, resize_pending_since TIMESTAMP)"
        ))
        db.execute(conductor_main.text(
            "INSERT INTO compute_servers (id, project_id, status) VALUES ('srv-1', 'proj-1', :s)"
        ), {"s": status})
        db.commit()
    monkeypatch.setattr(conductor_main, "_publish_server_event", lambda *a, **k: None)
    monkeypatch.setattr(conductor_main, "_lifecycle_server", lambda *a, **k: None)


def _pending():
    with conductor_main.SessionLocal() as db:
        return db.execute(conductor_main.text(
            "SELECT resize_pending_since FROM compute_servers WHERE id = 'srv-1'"
        )).scalar()


def test_entering_verify_resize_sets_timestamp(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    assert _pending() is None
    res = conductor_main.handle_update_server({"server_id": "srv-1", "fields": {"status": "VERIFY_RESIZE"}})
    assert res["ok"] is True
    assert res["updated"] == ["status"]
    assert _pending() is not None


def test_leaving_verify_resize_clears_timestamp(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    conductor_main.handle_update_server({"server_id": "srv-1", "fields": {"status": "VERIFY_RESIZE"}})
    assert _pending() is not None
    conductor_main.handle_update_server({"server_id": "srv-1", "fields": {"status": "ACTIVE"}})
    assert _pending() is None


def test_other_updates_leave_timestamp_alone(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    conductor_main.handle_update_server({"server_id": "srv-1", "fields": {"status": "VERIFY_RESIZE"}})
    before = _pending()
    assert before is not None
    res = conductor_main.handle_update_server({"server_id": "srv-1", "fields": {"error_message": "boom"}})
    assert res["updated"] == ["error_message"]
    assert _pending() == before


def test_repeating_verify_resize_keeps_the_first_timestamp(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    conductor_main.handle_update_server({"server_id": "srv-1", "fields": {"status": "VERIFY_RESIZE"}})
    with conductor_main.SessionLocal() as db:
        db.execute(conductor_main.text("UPDATE compute_servers SET resize_pending_since = '2026-01-01 00:00:00'"))
        db.commit()
    before = _pending()
    conductor_main.handle_update_server({"server_id": "srv-1", "fields": {"status": "VERIFY_RESIZE"}})
    assert _pending() == before


def test_verify_resize_without_a_timestamp_gets_one(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, status="VERIFY_RESIZE")
    assert _pending() is None
    conductor_main.handle_update_server({"server_id": "srv-1", "fields": {"status": "VERIFY_RESIZE"}})
    assert _pending() is not None


def test_verify_resize_to_error_clears_timestamp(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    conductor_main.handle_update_server({"server_id": "srv-1", "fields": {"status": "VERIFY_RESIZE"}})
    assert _pending() is not None
    conductor_main.handle_update_server({"server_id": "srv-1", "fields": {"status": "ERROR"}})
    assert _pending() is None


def test_migration_backfills_existing_verify_resize_rows(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'mig.sqlite'}")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE compute_servers (id VARCHAR PRIMARY KEY, status VARCHAR)"))
        conn.execute(sa.text("INSERT INTO compute_servers VALUES ('a', 'VERIFY_RESIZE'), ('b', 'ACTIVE')"))

    spec = importlib.util.spec_from_file_location("compute_0005", MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.revision == "compute_0005"
    assert mod.down_revision == "compute_0004"

    for _ in range(2):  # second run proves it is idempotent
        with engine.begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                mod.upgrade()

    with engine.connect() as conn:
        rows = dict(conn.execute(sa.text("SELECT id, resize_pending_since FROM compute_servers")).all())
    assert rows["a"] is not None
    assert rows["b"] is None
