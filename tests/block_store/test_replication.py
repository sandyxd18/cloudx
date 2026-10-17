from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient

from tests.stack import fresh_service


def test_block_store_volume_replication_dr(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "block_store_rep.sqlite"
    volume_root = tmp_path / "volumes"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("STORAGE_LVM_DRIVER", "true")

    bs_main = fresh_service("block_store", monkeypatch, tmp_path)
    monkeypatch.setattr("services.block_store.app.clients.cast_to_worker", lambda *a, **k: None)

    try:
        from shared.auth_middleware import require_auth
    except ImportError:
        from shared.auth_middleware import require_auth

    bs_main.app.dependency_overrides[require_auth] = lambda: {"project_id": "proj-1", "role": "admin"}

    client = TestClient(bs_main.app)

    db = bs_main.SessionLocal()
    volume_path = volume_root / "vol-dr.qcow2"
    volume_path.parent.mkdir(parents=True, exist_ok=True)
    volume_path.write_bytes(b"data-content-for-dr")
    vt = bs_main.VolumeType(id="vt-default", name="default", backend="lvm", is_public=True)
    volume = bs_main.Volume(id="vol-dr-1", name="vol-dr-1", size_gb=10, status="available", path=str(volume_path), bootable=False)
    db.add_all([vt, volume])
    db.commit()

    # 1. Enable Volume Replication
    rep_body = {"target": "dr-pool-node-2", "mode": "async"}
    res = client.post("/volumes/vol-dr-1/enable-replication", json=rep_body, headers={"X-Project-Id": "proj-1", "X-Role": "admin"})
    assert res.status_code == 200
    vol_rep = res.json()
    assert vol_rep["replication_status"] == "enabled"
    assert vol_rep["replication_target"] == "dr-pool-node-2"

    # 2. Sync Replication (Manual or background trigger)
    res = client.post("/volumes/vol-dr-1/sync-replication", headers={"X-Project-Id": "proj-1", "X-Role": "admin"})
    assert res.status_code == 200
    sync_res = res.json()
    assert sync_res["status"] in ("synced", "replicating")
    
    # Simulate sync completion by worker / conductor update
    db = bs_main.SessionLocal()
    v = db.get(bs_main.Volume, "vol-dr-1")
    v.replication_status = "synced"
    v.last_replicated_at = datetime.utcnow()
    db.commit()

    # 3. Failover Volume (Disaster Recovery promotion)
    res = client.post("/volumes/vol-dr-1/failover", headers={"X-Project-Id": "proj-1", "X-Role": "admin"})
    assert res.status_code == 200
    failover_res = res.json()
    assert failover_res["replication_status"] == "failed-over"

    # 4. Disable Replication
    res = client.post("/volumes/vol-dr-1/disable-replication", headers={"X-Project-Id": "proj-1", "X-Role": "admin"})
    assert res.status_code == 200
    assert res.json()["replication_status"] == "disabled"
