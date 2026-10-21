import pytest
import requests
from services.networking.app import reconciler
from shared import catalog


def _capture(monkeypatch, name):
    seen = []

    class Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"networks": []}

    monkeypatch.setattr(requests, name, lambda url, **kw: seen.append(url) or Resp())
    return seen


def test_reconciler_reaches_networking_through_the_catalog(monkeypatch):
    monkeypatch.delenv("NETWORKING_API_URL", raising=False)
    asked = []
    monkeypatch.setattr(catalog, "url", lambda service, interface="internal": asked.append(service) or "http://catalog-host:8001")
    seen = _capture(monkeypatch, "get")
    reconciler.reconcile_networks()
    assert asked == ["network"]
    assert seen == ["http://catalog-host:8001/networks"]


def test_reconciler_status_patch_goes_to_the_catalog_host(monkeypatch):
    monkeypatch.delenv("NETWORKING_API_URL", raising=False)
    monkeypatch.setattr(catalog, "url", lambda service, interface="internal": "http://catalog-host:8001")
    seen = _capture(monkeypatch, "patch")
    reconciler._patch_network_status("n1", "ACTIVE")
    assert seen == ["http://catalog-host:8001/networks/n1"]


def test_reconciler_env_override_still_wins(monkeypatch):
    monkeypatch.setenv("NETWORKING_API_URL", "http://override-host:1")
    monkeypatch.setattr(catalog, "_catalog", lambda service: pytest.fail("the override must not consult the catalog"))
    seen = _capture(monkeypatch, "get")
    reconciler.reconcile_networks()
    assert seen == ["http://override-host:1/networks"]


def test_reconciler_survives_a_catalog_failure(monkeypatch, caplog):
    monkeypatch.delenv("NETWORKING_API_URL", raising=False)

    def down(service, interface="internal"):
        raise catalog.CatalogError(f"cannot resolve service '{service}'")

    monkeypatch.setattr(catalog, "url", down)
    reconciler.reconcile_networks()
    assert "cannot resolve service 'network'" in caplog.text
