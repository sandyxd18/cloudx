import pytest
import requests
from services.image_worker.app import reconciler
from shared import catalog


def _down(service, interface="internal"):
    raise catalog.CatalogError(f"cannot resolve service '{service}'")


def _capture(monkeypatch, name, body=None):
    seen = []

    class Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return body if body is not None else {"items": []}

    monkeypatch.setattr(requests, name, lambda url, **kw: seen.append(url) or Resp())
    return seen


def test_list_goes_to_the_catalog_host(monkeypatch):
    monkeypatch.delenv("IMAGE_API_URL", raising=False)
    asked = []
    monkeypatch.setattr(catalog, "url", lambda service, interface="internal": asked.append(service) or "http://catalog-host:8002/")
    seen = _capture(monkeypatch, "get")
    reconciler.reconcile_images()
    assert asked == ["image"]
    assert seen == ["http://catalog-host:8002/images"]  # trailing slash stripped, as before


def test_patch_and_delete_go_to_the_catalog_host(monkeypatch):
    monkeypatch.delenv("IMAGE_API_URL", raising=False)
    monkeypatch.setattr(catalog, "url", lambda service, interface="internal": "http://catalog-host:8002")
    patched = _capture(monkeypatch, "patch")
    deleted = _capture(monkeypatch, "delete")
    reconciler._patch_image_status("i1", "error")
    reconciler._delete_image_record("i1")
    assert patched == ["http://catalog-host:8002/images/i1"]
    assert deleted == ["http://catalog-host:8002/images/i1"]


def test_env_override_still_wins(monkeypatch):
    monkeypatch.setenv("IMAGE_API_URL", "http://override-host:1/")
    monkeypatch.setattr(catalog, "_catalog", lambda service: pytest.fail("the override must not consult the catalog"))
    seen = _capture(monkeypatch, "get")
    reconciler.reconcile_images()
    assert seen == ["http://override-host:1/images"]


def test_pass_survives_a_catalog_failure_and_recovers(monkeypatch, caplog):
    monkeypatch.delenv("IMAGE_API_URL", raising=False)
    monkeypatch.setattr(catalog, "url", _down)
    seen = _capture(monkeypatch, "get")
    reconciler.reconcile_images()
    assert "cannot resolve service 'image'" in caplog.text
    assert seen == []
    monkeypatch.setattr(catalog, "url", lambda service, interface="internal": "http://catalog-host:8002")
    reconciler.reconcile_images()
    assert seen == ["http://catalog-host:8002/images"]


def test_status_patch_with_catalog_failure_only_logs(monkeypatch, caplog):
    monkeypatch.delenv("IMAGE_API_URL", raising=False)
    monkeypatch.setattr(catalog, "url", _down)
    reconciler._patch_image_status("i1", "error")
    reconciler._delete_image_record("i1")
    assert caplog.text.count("cannot resolve service 'image'") == 2
