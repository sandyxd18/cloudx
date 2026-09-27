"""
Tests for Phase 3: Production Features & UX
- SSL/TLS Termination on Load Balancer
- Scheduled Volume Snapshots & Auto-Retention in block-store-api
- Interactive Web Serial Console WebSocket in compute-api
"""

import importlib
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


def test_load_balancer_ssl_certificate(monkeypatch, tmp_path):
    db_path = tmp_path / "lb_ssl.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    import services.load_balancer.app.main as lb_main

    lb_main = importlib.reload(lb_main)
    lb_main.Base.metadata.create_all(bind=lb_main.engine)

    from shared.auth_middleware import require_auth
    lb_main.app.dependency_overrides[require_auth] = lambda: {"project_id": "proj-1", "role": "admin"}
    client = TestClient(lb_main.app)

    # Seed LB
    with lb_main.SessionLocal() as db:
        lb = lb_main.LoadBalancer(id="lb-ssl-1", name="lb-https", project_id="proj-1", lb_type="ALB", vip_address="10.0.0.100")
        db.add(lb)
        db.commit()

    # Upload certificate
    res_cert = client.post(
        "/load-balancers/lb-ssl-1/certificates",
        json={
            "name": "my-ssl-cert",
            "certificate_pem": "-----BEGIN CERTIFICATE-----\nFAKE_CERT\n-----END CERTIFICATE-----",
            "private_key_pem": "-----BEGIN PRIVATE KEY-----\nFAKE_KEY\n-----END PRIVATE KEY-----",
        },
    )
    assert res_cert.status_code == 201
    cert_id = res_cert.json()["id"]

    # Create HTTPS listener with certificate_id
    res_ls = client.post(
        "/load-balancers/lb-ssl-1/listeners",
        json={"name": "https-443", "protocol": "HTTPS", "protocol_port": 443, "certificate_id": cert_id},
    )
    assert res_ls.status_code == 201
    data = res_ls.json()
    assert data["certificate_id"] == cert_id
    assert data["ssl_cert_path"] is not None
    assert Path(data["ssl_cert_path"]).exists()


def test_block_store_scheduled_snapshots(monkeypatch, tmp_path):
    db_path = tmp_path / "block_schedule.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    import services.block_store.app.main as bs_main

    bs_main = importlib.reload(bs_main)
    bs_main.Base.metadata.create_all(bind=bs_main.engine)

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


def test_compute_serial_console_websocket(monkeypatch, tmp_path):
    db_path = tmp_path / "compute_ws.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    import services.compute.app.main as compute_main

    compute_main = importlib.reload(compute_main)
    compute_main.Base.metadata.create_all(bind=compute_main.engine)

    client = TestClient(compute_main.app)

    with client.websocket_connect("/servers/srv-123/console/ws") as websocket:
        msg = websocket.receive_text()
        assert "Connected to CloudX Serial Console" in msg
        websocket.send_text("ls -la")
        resp = websocket.receive_text()
        assert resp == "echo: ls -la"
