from types import SimpleNamespace

import pytest
from services.event_worker.app import main as w
from shared import catalog


def _down(service, interface="internal"):
    raise catalog.CatalogError(f"cannot resolve service '{service}'")


def test_event_api_calls_go_to_the_catalog_host(monkeypatch):
    monkeypatch.delenv("EVENT_API_URL", raising=False)
    monkeypatch.setattr(catalog, "url", lambda service, interface="internal": "http://catalog-host:8012/")
    seen = []
    monkeypatch.setattr(w.requests, "request", lambda method, url, **kw: seen.append((method, url)) or SimpleNamespace(
        status_code=200, json=lambda: []))
    assert w._get_pending_deliveries() == []
    assert seen == [("GET", "http://catalog-host:8012/events/deliveries")]


def test_env_override_still_wins(monkeypatch):
    monkeypatch.setenv("EVENT_API_URL", "http://override-host:1/")
    monkeypatch.setattr(catalog, "_catalog", lambda service: pytest.fail("the override must not consult the catalog"))
    seen = []
    monkeypatch.setattr(w.requests, "request", lambda method, url, **kw: seen.append(url) or SimpleNamespace(
        status_code=200, json=lambda: []))
    w._get_pending_deliveries()
    assert seen == ["http://override-host:1/events/deliveries"]


def test_catalog_failure_polls_nothing_and_the_cycle_ends_quietly(monkeypatch, caplog):
    monkeypatch.delenv("EVENT_API_URL", raising=False)
    monkeypatch.setattr(catalog, "url", _down)
    monkeypatch.setattr(w.requests, "request", lambda *a, **k: pytest.fail("no request without a resolved URL"))
    w.process_deliveries()
    assert w._get_subscription(1) is None
    assert w._update_delivery(1, "failed", None, 1) is False
    assert caplog.text.count("cannot resolve service 'event'") == 3
