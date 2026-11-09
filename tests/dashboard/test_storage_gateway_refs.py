"""The storage gateway sends the bucket and the object key to object-store as path segments, never as URL syntax."""
import importlib

import pytest
from fastapi.testclient import TestClient
from shared import catalog


@pytest.fixture
def gateway(monkeypatch):
    dashboard = importlib.import_module("services.dashboard.app.main")
    monkeypatch.delenv("OBJECT_STORE_API_URL", raising=False)
    monkeypatch.setattr(catalog, "url", lambda service, interface="internal": "http://store:8005")
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
    return type("G", (), {"client": TestClient(dashboard.app), "seen": seen})


@pytest.mark.parametrize("path", ["/storage/b1/a/b/c.txt", "/storage/b1/photo%20one.png", "/storage/b1/a%C3%A9.txt"])
def test_a_normal_key_is_sent_as_before(gateway, path):
    assert gateway.client.get(path).status_code == 200
    assert gateway.seen[-1].startswith("http://store:8005/buckets/b1/objects/")


def test_a_nested_key_keeps_its_slashes(gateway):
    gateway.client.get("/storage/b1/a/b/c.txt")

    assert gateway.seen == ["http://store:8005/buckets/b1/objects/a/b/c.txt"]


@pytest.mark.parametrize("path", ["/storage/b1/a%3Fx=1", "/storage/b1/a%23frag"])
def test_a_key_with_url_syntax_stays_one_path(gateway, path):
    gateway.client.get(path)

    url = gateway.seen[-1]
    assert "?" not in url and "#" not in url
    assert url.startswith("http://store:8005/buckets/b1/objects/a%")


@pytest.mark.parametrize("path", ["/storage/b1/%2e%2e/x", "/storage/b1/a/%2e%2e/%2e%2e/x", "/storage/b1/a/%2e/b",
                                  "/storage/b1/a%5Cb"])
def test_a_key_with_dot_segments_or_a_backslash_is_refused(gateway, path):
    r = gateway.client.get(path)

    assert r.status_code == 404
    assert gateway.seen == []


@pytest.mark.parametrize("path", ["/storage/%2e%2e/", "/storage/%2e%2e/x", "/storage/%2e/x"])
def test_a_bucket_of_dots_is_refused(gateway, path):
    assert gateway.client.get(path).status_code == 404
    assert gateway.seen == []


@pytest.mark.parametrize("path", ["/storage/b%3Fx/", "/storage/b%23x/k"])
def test_a_bucket_with_url_syntax_stays_one_segment(gateway, path):
    gateway.client.get(path)

    url = gateway.seen[-1]
    assert url.startswith("http://store:8005/buckets/b%")
    assert "?" not in url and "#" not in url
