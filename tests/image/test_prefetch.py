import importlib

from fastapi.testclient import TestClient


def test_image_prefetch_endpoint(monkeypatch, tmp_path):
    db_path = tmp_path / "image_prefetch.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    import services.image.app.main as image_main

    image_main = importlib.reload(image_main)
    image_main.Base.metadata.create_all(bind=image_main.engine)

    from shared.auth_middleware import require_auth
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
