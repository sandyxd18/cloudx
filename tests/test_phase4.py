"""
Tests for Phase 4: Operations & Advanced Optimizations
- Image Caching & Prefetching in compute-worker and image-api
- Security Group Presets in networking-api
- Centralized Audit Logging System in auth-api
"""

import importlib
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


def test_compute_image_prefetch_handler(monkeypatch):
    import services.compute_worker.app.main as worker_main

    downloaded = []

    monkeypatch.setattr(
        "services.compute_worker.app.image_manager.fetch_image_meta",
        lambda ref: {"id": ref, "size": 1024, "filename": "cirros.qcow2"},
    )
    monkeypatch.setattr(
        "services.compute_worker.app.image_manager.download_image",
        lambda meta: downloaded.append(meta["id"]) or Path("/var/lib/mini-openstack/compute/cache/images/cirros.qcow2"),
    )

    res = worker_main.handle_prefetch_image({"image_id": "img-cirros-1"})
    assert res["status"] == "cached"
    assert res["image_id"] == "img-cirros-1"
    assert "img-cirros-1" in downloaded


def test_image_prefetch_endpoint(monkeypatch, tmp_path):
    db_path = tmp_path / "image_prefetch.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    import services.image.app.main as image_main

    image_main = importlib.reload(image_main)
    image_main.Base.metadata.create_all(bind=image_main.engine)

    from services.shared.auth_middleware import require_auth
    image_main.app.dependency_overrides[require_auth] = lambda: {"project_id": "proj-1", "role": "admin"}
    client = TestClient(image_main.app)

    # Seed image
    with image_main.SessionLocal() as db:
        img = image_main.Image(
            id="img-101",
            name="ubuntu-22.04",
            filename="ubuntu-22.04.qcow2",
            object_name="ubuntu-22.04.qcow2",
            disk_format="qcow2",
            container_format="bare",
            size=1024,
            checksum="abc123sha256",
            status="ACTIVE",
            project_id="proj-1",
        )
        db.add(img)
        db.commit()

    class DummyRPC:
        def cast(self, target, method, args):
            rpc_casts.append((target, method, args))

    rpc_casts = []
    monkeypatch.setattr(image_main, "_get_rpc", lambda: DummyRPC())

    res = client.post("/images/img-101/prefetch")
    assert res.status_code == 200
    assert res.json()["status"] == "prefetching"
    assert rpc_casts == [("compute", "prefetch_image", {"image_id": "img-101"})]


def test_security_group_presets(monkeypatch, tmp_path):
    db_path = tmp_path / "networking_preset.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    import services.networking.app.main as net_main

    net_main = importlib.reload(net_main)
    net_main.Base.metadata.create_all(bind=net_main.engine)

    try:
        from shared.auth_middleware import require_auth
    except ImportError:
        from services.shared.auth_middleware import require_auth

    net_main.app.dependency_overrides[require_auth] = lambda: {"project_id": "proj-1", "role": "admin"}
    client = TestClient(net_main.app)

    # Mock OVN command
    monkeypatch.setattr(net_main, "ovn", lambda *args, **kwargs: "")

    # GET presets
    res_presets = client.get("/security-groups/presets")
    assert res_presets.status_code == 200
    presets = res_presets.json()["presets"]
    assert "ssh" in presets
    assert "web" in presets

    # Create security group
    res_sg = client.post("/security-groups", json={"name": "web-sg", "description": "Web SG"})
    assert res_sg.status_code == 201
    sg_id = res_sg.json()["id"]

    # Apply 'web' preset
    res_apply = client.post(f"/security-groups/{sg_id}/apply-preset", json={"preset": "web"})
    assert res_apply.status_code == 200
    assert res_apply.json()["rules_added"] == 2

    # Check security group detail rules
    res_detail = client.get(f"/security-groups/{sg_id}")
    assert res_detail.status_code == 200
    rules = res_detail.json()["security_group_rules"]
    assert len(rules) == 2


def test_audit_logging(monkeypatch, tmp_path):
    db_path = tmp_path / "auth_audit.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    import services.auth.app.main as auth_main

    auth_main = importlib.reload(auth_main)
    auth_main.Base.metadata.create_all(bind=auth_main.engine)

    try:
        from shared.auth_middleware import require_auth
    except ImportError:
        from services.shared.auth_middleware import require_auth

    auth_main.app.dependency_overrides[auth_main._require_token] = lambda: {"project_id": "proj-1", "role": "admin", "username": "admin"}
    client = TestClient(auth_main.app)

    # Create audit log entry
    res_create = client.post(
        "/audit-logs",
        json={
            "username": "admin",
            "project_id": "proj-1",
            "action": "server.create",
            "resource_type": "server",
            "resource_id": "srv-999",
            "details": "Created VM instance srv-999",
        },
    )
    assert res_create.status_code == 201
    data = res_create.json()
    assert data["action"] == "server.create"
    assert data["resource_id"] == "srv-999"

    # List audit logs
    res_list = client.get("/audit-logs?project_id=proj-1")
    assert res_list.status_code == 200
    items = res_list.json()["items"]
    assert len(items) == 1
    assert items[0]["resource_type"] == "server"
