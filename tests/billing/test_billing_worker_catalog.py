from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from services.billing_worker.app import main as w
from shared import catalog

S = datetime(2026, 10, 1, 10, tzinfo=timezone.utc)
E = datetime(2026, 10, 1, 11, tzinfo=timezone.utc)
PEERS = ("COMPUTE_API_URL", "BLOCK_STORE_API_URL", "NETWORKING_API_URL", "LOAD_BALANCER_API_URL", "BILLING_API_URL", "EVENT_API_URL")


@pytest.fixture(autouse=True)
def no_overrides(monkeypatch):
    for key in PEERS:
        monkeypatch.delenv(key, raising=False)


def _catalog_hosts(monkeypatch, **hosts):
    asked = []

    def url(service, interface="internal"):
        asked.append(service)
        if service not in hosts:
            raise catalog.CatalogError(f"cannot resolve service '{service}'")
        return hosts[service]

    monkeypatch.setattr(catalog, "url", url)
    return asked


def _requests(monkeypatch, body=None):
    seen = []
    monkeypatch.setattr(w.requests, "request", lambda method, url, **kw: seen.append((method, url)) or SimpleNamespace(
        status_code=200, json=lambda: body if body is not None else [], text=""))
    return seen


def test_listings_go_to_the_catalog_hosts(monkeypatch):
    _catalog_hosts(monkeypatch, compute="http://c:8000/", volume="http://v:8000", network="http://n:8000", **{"load-balancer": "http://l:8008"})
    seen = _requests(monkeypatch)
    for fetch in (w._get_active_servers, w._get_volumes, w._get_floating_ips, w._get_load_balancers):
        assert fetch() == []
    assert [url for _, url in seen] == [
        "http://c:8000/servers?all_tenants=true", "http://v:8000/volumes?all_tenants=true",
        "http://n:8000/floating-ips?all_tenants=true", "http://l:8008/load-balancers?all_tenants=true"]


def test_env_override_still_wins(monkeypatch):
    monkeypatch.setenv("COMPUTE_API_URL", "http://override-host:1/")
    monkeypatch.setattr(catalog, "_catalog", lambda service: pytest.fail("the override must not consult the catalog"))
    seen = _requests(monkeypatch)
    assert w._get_active_servers() == []
    assert seen == [("GET", "http://override-host:1/servers?all_tenants=true")]


def test_unresolvable_listing_is_none_so_the_snapshot_is_kept(monkeypatch, caplog):
    _catalog_hosts(monkeypatch)
    seen = _requests(monkeypatch)
    assert w._get_active_servers() is None
    assert seen == []
    assert "cannot resolve service 'compute'" in caplog.text


def test_usage_records_and_cleanup_survive_a_catalog_failure(monkeypatch, caplog):
    _catalog_hosts(monkeypatch)
    seen = _requests(monkeypatch)
    assert w._send_usage_records([{"resource_id": "r"}]) is False
    w._cleanup_old_records()
    assert seen == []
    assert caplog.text.count("cannot resolve service 'billing'") == 2


def test_usage_records_go_to_the_billing_host(monkeypatch):
    _catalog_hosts(monkeypatch, billing="http://b:8011/")
    seen = _requests(monkeypatch, body={"created": 1, "skipped": 0})
    assert w._send_usage_records([{"resource_id": "r"}]) is True
    assert seen == [("POST", "http://b:8011/billing/usage-records/batch")]


def test_vanished_resources_fall_back_to_the_estimate_with_one_catalog_try(monkeypatch):
    asked = _catalog_hosts(monkeypatch)
    seen = _requests(monkeypatch)
    state = {"servers": {k: {"id": k, "project_id": "p"} for k in "abc"}}
    out = w._with_ghosts("servers", [], state, S, E)
    assert [r["deleted_at"] for r in out] == ["2026-10-01T10:30:00+00:00"] * 3
    assert asked == ["event"] and seen == []
