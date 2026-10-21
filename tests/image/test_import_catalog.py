import pytest
from services.image_worker.app import main as worker
from shared import catalog


def test_import_with_an_unresolvable_image_api_downloads_nothing(monkeypatch):
    monkeypatch.delenv("IMAGE_API_URL", raising=False)

    def down(service, interface="internal"):
        raise catalog.CatalogError(f"cannot resolve service '{service}'")

    monkeypatch.setattr(catalog, "url", down)
    monkeypatch.setattr(worker.requests, "get", lambda *a, **k: pytest.fail("no download before image-api resolves"))
    with pytest.raises(catalog.CatalogError, match="cannot resolve service 'image'"):
        worker.handle_import_image_task({"task_id": "t1", "image_id": "i1", "import_from": "http://src/x.qcow2"})


def test_task_and_image_patches_go_to_the_catalog_host(monkeypatch):
    monkeypatch.delenv("IMAGE_API_URL", raising=False)
    monkeypatch.setattr(catalog, "url", lambda service, interface="internal": "http://catalog-host:8002/")
    seen = []
    monkeypatch.setattr(worker.requests, "patch", lambda url, **kw: seen.append(url))
    worker._patch_task("t1", {"status": "processing"})
    worker._patch_image("i1", {"status": "ACTIVE"})
    assert seen == ["http://catalog-host:8002/v2/tasks/t1", "http://catalog-host:8002/images/i1"]
