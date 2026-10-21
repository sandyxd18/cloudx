import pytest
import requests
from shared import catalog
from tests.stack import fresh_service


@pytest.fixture
def compute(monkeypatch, tmp_path):
    return fresh_service("compute", monkeypatch, tmp_path)


def _capture(monkeypatch, name):
    seen = []

    class Resp:
        status_code = 200

        def json(self):
            return {"detached": []}

    monkeypatch.setattr(requests, name, lambda url, **kw: seen.append(url) or Resp())
    return seen


def test_block_store_url_comes_from_the_catalog(compute, monkeypatch):
    monkeypatch.delenv("BLOCK_STORE_API_URL", raising=False)
    asked = []
    monkeypatch.setattr(catalog, "url", lambda service, interface="internal": asked.append(service) or "http://catalog-host:8004")
    seen = _capture(monkeypatch, "post")
    assert compute.clients.release_server_volumes("srv1") == {"detached": []}
    assert asked == ["volume"]
    assert seen == ["http://catalog-host:8004/servers/srv1/volumes/detach-all"]


def test_block_store_helpers_use_the_catalog(compute, monkeypatch):
    monkeypatch.delenv("BLOCK_STORE_API_URL", raising=False)
    monkeypatch.setattr(catalog, "url", lambda service, interface="internal": "http://catalog-host:8004/")
    seen = _capture(monkeypatch, "get")
    compute.clients.block_get("/volumes")
    assert seen == ["http://catalog-host:8004/volumes"]


def test_block_store_url_override_still_wins(compute, monkeypatch):
    monkeypatch.setenv("BLOCK_STORE_API_URL", "http://override-host:1")
    monkeypatch.setattr(catalog, "_catalog", lambda service: pytest.fail("the override must not consult the catalog"))
    seen = _capture(monkeypatch, "post")
    compute.clients.release_server_volumes("srv1")
    assert seen == ["http://override-host:1/servers/srv1/volumes/detach-all"]


def test_release_server_volumes_is_best_effort_when_the_catalog_is_down(compute, monkeypatch):
    monkeypatch.delenv("BLOCK_STORE_API_URL", raising=False)

    def down(service, interface="internal"):
        raise catalog.CatalogError(f"cannot resolve service '{service}'")

    monkeypatch.setattr(catalog, "url", down)
    assert compute.clients.release_server_volumes("srv1") is None


def test_placement_url_comes_from_the_catalog(compute, monkeypatch):
    monkeypatch.delenv("PLACEMENT_API_URL", raising=False)
    monkeypatch.setattr(compute.service, "COMPUTE_HOSTS", [])
    monkeypatch.setattr(catalog, "url", lambda service, interface="internal": "http://catalog-host:8006")
    seen = []

    class Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"resource_providers": [{"name": "node-1"}]}

    monkeypatch.setattr(requests, "get", lambda url, **kw: seen.append(url) or Resp())
    assert compute.service.compute_hosts() == ["node-1"]
    assert seen == ["http://catalog-host:8006/resource_providers"]


def test_compute_hosts_fall_back_to_any_worker_when_the_catalog_is_down(compute, monkeypatch):
    monkeypatch.delenv("PLACEMENT_API_URL", raising=False)
    monkeypatch.setattr(compute.service, "COMPUTE_HOSTS", [])

    def down(service, interface="internal"):
        raise catalog.CatalogError(f"cannot resolve service '{service}'")

    monkeypatch.setattr(catalog, "url", down)
    assert compute.service.compute_hosts() == []
