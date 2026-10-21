import pytest
from services.compute_worker.app.config import endpoint
from shared import catalog


def _down(service, interface="internal"):
    raise catalog.CatalogError(f"cannot resolve service '{service}'")


@pytest.mark.parametrize("env, cat, message, default, expected", [
    ("http://env-host:1/", "http://cat-host:2", "http://msg-host:3", "http://dflt:4", "http://env-host:1"),
    (None, "http://cat-host:2/", "http://msg-host:3", "http://dflt:4", "http://cat-host:2"),
    (None, "down", "http://msg-host:3/", "http://dflt:4", "http://msg-host:3"),
    (None, "down", None, "http://dflt:4/", "http://dflt:4"),
])
def test_endpoint_prefers_worker_env_then_catalog_then_message(monkeypatch, env, cat, message, default, expected):
    if env:
        monkeypatch.setenv("BLOCK_STORE_API_URL", env)
    else:
        monkeypatch.delenv("BLOCK_STORE_API_URL", raising=False)

    def fake_catalog(service):
        if cat == "down":
            raise catalog.CatalogError(f"cannot resolve service '{service}'")
        return [{"name": "volume", "endpoints": [{"interface": "internal", "url": cat}]}]

    monkeypatch.setattr(catalog, "_catalog", fake_catalog)
    args = {"_block_store_url": message} if message else {}
    assert endpoint(args, "_block_store_url", "BLOCK_STORE_API_URL", default) == expected


@pytest.mark.parametrize("env_name, name", [
    ("IMAGE_API_URL", "image"), ("BLOCK_STORE_API_URL", "volume"), ("NETWORKING_API_URL", "network"),
])
def test_endpoint_asks_the_catalog_for_the_matching_service(monkeypatch, env_name, name):
    monkeypatch.delenv(env_name, raising=False)
    asked = []
    monkeypatch.setattr(catalog, "url", lambda service, interface="internal": asked.append(service) or "http://c:1")
    assert endpoint({}, "_x", env_name) == "http://c:1"
    assert asked == [name]


def test_the_real_override_wins_without_touching_the_catalog(monkeypatch):
    monkeypatch.setenv("IMAGE_API_URL", "http://env-host:1/")
    monkeypatch.setattr(catalog, "_catalog", lambda service: pytest.fail("the override must not consult the catalog"))
    assert endpoint({"_i": "http://127.0.0.1:2"}, "_i", "IMAGE_API_URL", "http://d:3") == "http://env-host:1"


def test_the_workers_own_environment_wins_over_the_address_in_the_message(monkeypatch):
    # compute-api sends the address as it reaches block-store (loopback); on another node that is the wrong machine
    monkeypatch.setenv("BLOCK_STORE_API_URL", "http://172.20.0.131:8004/")
    args = {"_block_store_url": "http://127.0.0.1:8004"}
    assert endpoint(args, "_block_store_url", "BLOCK_STORE_API_URL", "http://block-store-api:8000") == "http://172.20.0.131:8004"


def test_without_a_worker_setting_the_message_is_used(monkeypatch):
    monkeypatch.delenv("BLOCK_STORE_API_URL", raising=False)
    monkeypatch.setattr(catalog, "url", _down)
    args = {"_block_store_url": "http://10.0.0.5:8004/"}
    assert endpoint(args, "_block_store_url", "BLOCK_STORE_API_URL", "http://block-store-api:8000") == "http://10.0.0.5:8004"


def test_the_default_is_the_last_resort(monkeypatch):
    monkeypatch.delenv("NETWORKING_API_URL", raising=False)
    monkeypatch.setattr(catalog, "url", _down)
    assert endpoint({}, "_networking_url", "NETWORKING_API_URL", "http://localhost:8001") == "http://localhost:8001"


def test_block_store_worker_downloads_images_from_its_own_configured_image_api(monkeypatch):
    from services.block_store_worker.app import main as worker

    seen = []
    monkeypatch.setenv("IMAGE_API_URL", "http://172.20.0.131:8002")
    monkeypatch.setattr(worker, "download_image", lambda ref, url: seen.append(url) or (_ for _ in ()).throw(RuntimeError("stop")))
    monkeypatch.setattr(worker, "conductor_mark_volume_error", lambda *a: None)
    worker.handle_create_volume({"volume_id": "v", "size_gb": 1, "backend": "lvm", "image_ref": "img", "_image_api_url": "http://127.0.0.1:8002"})
    assert seen == ["http://172.20.0.131:8002"]
