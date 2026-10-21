from types import SimpleNamespace

import pytest
from services.k8s_engine_worker.app import main as w
from shared import catalog

from tests.k8s_engine_worker.test_reconcile import cluster


@pytest.fixture(autouse=True)
def no_overrides(monkeypatch):
    for key in ("K8S_ENGINE_API_URL", "COMPUTE_API_URL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("INTERNAL_TOKEN", "internal_0123456789")


def _hosts(monkeypatch, **hosts):
    def url(service, interface="internal"):
        if service not in hosts:
            raise catalog.CatalogError(f"cannot resolve service '{service}'")
        return hosts[service]

    monkeypatch.setattr(catalog, "url", url)


def _http(monkeypatch, clusters):
    seen = []

    def resp(body=None, code=200):
        return SimpleNamespace(status_code=code, json=lambda: body, raise_for_status=lambda: None)

    monkeypatch.setattr(w.requests, "get", lambda url, **k: seen.append(("GET", url)) or resp(clusters))
    monkeypatch.setattr(w.requests, "post", lambda url, **k: seen.append(("POST", url)) or resp({"id": "srv-1"}, 201))
    monkeypatch.setattr(w.requests, "delete", lambda url, **k: seen.append(("DELETE", url)) or resp(None, 204))
    return seen


def test_calls_go_to_the_catalog_hosts(monkeypatch):
    _hosts(monkeypatch, **{"k8s-engine": "http://k:8015/", "compute": "http://c:8000/"})
    seen = _http(monkeypatch, [cluster()])
    w.tick()
    assert seen == [("GET", "http://k:8015/internal/k8s-engine/clusters"),
                    ("POST", "http://c:8000/servers"),
                    ("POST", "http://k:8015/internal/k8s-engine/clusters/cls-1234567890ab/nodes")]


def test_unresolvable_compute_creates_nothing_and_the_tick_survives(monkeypatch, caplog):
    _hosts(monkeypatch, **{"k8s-engine": "http://k:8015"})
    seen = _http(monkeypatch, [cluster()])
    w.tick()
    assert [c for c in seen if c[0] == "POST" and c[1].endswith("/servers")] == []
    assert "cannot resolve service 'compute'" in caplog.text


def test_unresolvable_k8s_engine_is_logged_like_an_unreachable_one(monkeypatch, caplog):
    _hosts(monkeypatch)
    seen = _http(monkeypatch, [])
    w.tick()
    assert seen == []
    assert "failed to fetch clusters" in caplog.text and "cannot resolve service 'k8s-engine'" in caplog.text
