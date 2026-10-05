from services.compute_worker.app.config import endpoint


def test_the_workers_own_environment_wins_over_the_address_in_the_message(monkeypatch):
    # compute-api sends the address as it reaches block-store (loopback); on another node that is the wrong machine
    monkeypatch.setenv("BLOCK_STORE_API_URL", "http://172.20.0.131:8004/")
    args = {"_block_store_url": "http://127.0.0.1:8004"}
    assert endpoint(args, "_block_store_url", "BLOCK_STORE_API_URL", "http://block-store-api:8000") == "http://172.20.0.131:8004"


def test_without_a_worker_setting_the_message_is_used(monkeypatch):
    monkeypatch.delenv("BLOCK_STORE_API_URL", raising=False)
    args = {"_block_store_url": "http://10.0.0.5:8004/"}
    assert endpoint(args, "_block_store_url", "BLOCK_STORE_API_URL", "http://block-store-api:8000") == "http://10.0.0.5:8004"


def test_the_default_is_the_last_resort(monkeypatch):
    monkeypatch.delenv("NETWORKING_API_URL", raising=False)
    assert endpoint({}, "_networking_url", "NETWORKING_API_URL", "http://localhost:8001") == "http://localhost:8001"


def test_block_store_worker_downloads_images_from_its_own_configured_image_api(monkeypatch):
    from services.block_store_worker.app import main as worker

    seen = []
    monkeypatch.setenv("IMAGE_API_URL", "http://172.20.0.131:8002")
    monkeypatch.setattr(worker, "download_image", lambda ref, url: seen.append(url) or (_ for _ in ()).throw(RuntimeError("stop")))
    monkeypatch.setattr(worker, "conductor_mark_volume_error", lambda *a: None)
    worker.handle_create_volume({"volume_id": "v", "size_gb": 1, "backend": "lvm", "image_ref": "img", "_image_api_url": "http://127.0.0.1:8002"})
    assert seen == ["http://172.20.0.131:8002"]
