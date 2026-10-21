import pytest
from services.block_store_worker.app import main as worker
from shared import catalog

ARGS = {"volume_id": "v", "size_gb": 1, "backend": "lvm", "image_ref": "img"}


def _create(monkeypatch, **extra):
    seen, errors = [], []

    def download(ref, url):
        seen.append(url)
        raise RuntimeError("stop")

    monkeypatch.setattr(worker, "download_image", download)
    monkeypatch.setattr(worker, "conductor_mark_volume_error", lambda *a: errors.append(a))
    worker.handle_create_volume({**ARGS, **extra})
    return seen, errors


def _catalog(monkeypatch, url):
    def fake(service):
        if url is None:
            raise catalog.CatalogError(f"cannot resolve service '{service}'")
        return [{"name": "image", "endpoints": [{"interface": "internal", "url": url}]}]

    monkeypatch.setattr(catalog, "_catalog", fake)


@pytest.mark.parametrize("env, cat, message, expected", [
    ("http://env-host:1/", "http://cat-host:2", "http://127.0.0.1:3", "http://env-host:1"),
    (None, "http://cat-host:2/", "http://127.0.0.1:3", "http://cat-host:2"),
    (None, None, "http://msg-host:3/", "http://msg-host:3"),
    (None, None, None, "http://image-api:8000"),
])
def test_image_api_url_prefers_worker_env_then_catalog_then_message_then_default(monkeypatch, env, cat, message, expected):
    if env:
        monkeypatch.setenv("IMAGE_API_URL", env)
    else:
        monkeypatch.delenv("IMAGE_API_URL", raising=False)
    _catalog(monkeypatch, cat)
    extra = {"_image_api_url": message} if message else {}
    seen, _ = _create(monkeypatch, **extra)
    assert seen == [expected]


def test_a_catalog_failure_does_not_stop_the_worker(monkeypatch):
    monkeypatch.delenv("IMAGE_API_URL", raising=False)
    _catalog(monkeypatch, None)
    seen, errors = _create(monkeypatch, _image_api_url="http://msg-host:3")
    assert seen == ["http://msg-host:3"] and len(errors) == 1  # the volume is marked in error by the failed download only


def test_a_blank_volume_does_not_consult_the_catalog(monkeypatch):
    monkeypatch.delenv("IMAGE_API_URL", raising=False)
    monkeypatch.setattr(catalog, "url", lambda *a, **k: pytest.fail("no image, no image-api"))
    created = []
    monkeypatch.setattr(worker.lvm, "create", lambda lv, gb, **k: created.append(lv) or "/dev/vg/x")
    monkeypatch.setattr(worker, "conductor_update_volume", lambda *a: None)
    worker.handle_create_volume({"volume_id": "v", "size_gb": 1, "backend": "lvm"})
    assert created
