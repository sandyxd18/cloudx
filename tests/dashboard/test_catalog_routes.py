import importlib

import pytest
from fastapi.testclient import TestClient
from shared import catalog


@pytest.fixture
def dashboard(monkeypatch):
    return importlib.import_module("services.dashboard.app.main")


class FakeHttp:
    def __init__(self):
        self.urls = []

    async def request(self, method, url, headers=None, content=None):
        self.urls.append(url)

        class Resp:
            content = b"{}"
            status_code = 200
            headers = {"content-type": "application/json"}

        return Resp()

    get = None


def test_proxy_routes_resolve_through_the_catalog(dashboard, monkeypatch):
    monkeypatch.delenv("COMPUTE_API_URL", raising=False)
    asked = []
    monkeypatch.setattr(catalog, "url", lambda service, interface="internal": asked.append(service) or "http://catalog-host:8003")
    assert dashboard._resolve_backend("/api/compute/servers") == ("http://catalog-host:8003", "/servers")
    assert asked == ["compute"]


def test_proxy_forwards_to_the_catalog_host(dashboard, monkeypatch):
    monkeypatch.delenv("IMAGE_API_URL", raising=False)
    monkeypatch.setattr(catalog, "url", lambda service, interface="internal": f"http://catalog-{service}:8002/")
    http = FakeHttp()
    monkeypatch.setattr(dashboard, "_http_client", http)
    assert TestClient(dashboard.app).get("/api/image/images").status_code == 200
    assert http.urls == ["http://catalog-image:8002/images"]


def test_proxy_env_override_still_wins(dashboard, monkeypatch):
    monkeypatch.setenv("COMPUTE_API_URL", "http://override-host:1/")
    monkeypatch.setattr(catalog, "_catalog", lambda service: pytest.fail("the override must not consult the catalog"))
    assert dashboard._resolve_backend("/api/compute/servers") == ("http://override-host:1", "/servers")


def test_auth_route_keeps_reading_auth_api_url(dashboard, monkeypatch):
    monkeypatch.setattr(catalog, "url", lambda *a, **k: pytest.fail("auth is not resolved through the catalog"))
    assert dashboard._resolve_backend("/api/auth/tokens") == (dashboard.AUTH_API_URL, "/tokens")


def test_unknown_prefix_is_still_unresolved(dashboard):
    assert dashboard._resolve_backend("/api/nope") is None


def test_storage_gateway_uses_the_object_store_catalog_entry(dashboard, monkeypatch):
    monkeypatch.delenv("OBJECT_STORE_API_URL", raising=False)
    monkeypatch.setattr(catalog, "url", lambda service, interface="internal": f"http://catalog-{service}:8005")
    seen = []

    class Http:
        async def get(self, url, headers=None):
            seen.append(url)

            class Resp:
                content = b""
                status_code = 200
                headers = {}

            return Resp()

    monkeypatch.setattr(dashboard, "_http_client", Http())
    TestClient(dashboard.app).get("/storage/b1/")
    assert seen == ["http://catalog-object-store:8005/buckets/b1/objects"]


def test_proxy_answers_502_when_the_catalog_cannot_resolve_the_service(dashboard, monkeypatch):
    monkeypatch.delenv("IMAGE_API_URL", raising=False)

    def down(service, interface="internal"):
        raise catalog.CatalogError(f"cannot resolve service '{service}'")

    monkeypatch.setattr(catalog, "url", down)
    r = TestClient(dashboard.app).get("/api/image/images")
    assert r.status_code == 502 and "cannot resolve service 'image'" in r.text
