from pathlib import Path


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
