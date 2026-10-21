import importlib
import sys
from types import SimpleNamespace

import pytest
from shared import catalog


def worker():
    sys.modules.setdefault("shared.proc", importlib.import_module("shared.proc"))
    return importlib.import_module("services.dns_worker.app.main")


@pytest.fixture(autouse=True)
def no_overrides(monkeypatch):
    for key in ("DNS_API_URL", "NETWORKING_API_URL", "LOAD_BALANCER_API_URL", "COMPUTE_API_URL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AUTH_API_URL", "http://auth.test:8005/")


def _hosts(monkeypatch, **hosts):
    def url(service, interface="internal"):
        if service not in hosts:
            raise catalog.CatalogError(f"cannot resolve service '{service}'")
        return hosts[service]

    monkeypatch.setattr(catalog, "url", url)


def _http(m, monkeypatch):
    seen = []

    def get(url, **kw):
        seen.append(url)
        body = {"projects": {}} if url.endswith("/internal/projects") else (
            {"records": {}} if url.endswith("/internal/records") else [])
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: body)

    monkeypatch.setattr(m.requests, "get", get)
    return seen


def test_collect_want_asks_the_catalog_but_keeps_auth_on_its_own_setting(monkeypatch):
    m = worker()
    _hosts(monkeypatch, network="http://n:8001/", **{"load-balancer": "http://l:8008"}, compute="http://c:8003")
    seen = _http(m, monkeypatch)
    assert m.collect_want() == []
    assert seen == ["http://auth.test:8005/internal/projects", "http://n:8001/floating-ips",
                    "http://l:8008/load-balancers", "http://c:8003/servers?limit=1000"]


def test_collect_want_resolves_every_peer_before_the_first_request(monkeypatch):
    m = worker()
    _hosts(monkeypatch, network="http://n:8001")  # compute and load-balancer are missing
    seen = _http(m, monkeypatch)
    with pytest.raises(catalog.CatalogError):
        m.collect_want()
    assert seen == []


def test_tick_and_sweep_use_the_dns_host_and_fail_cleanly_without_it(monkeypatch):
    m = worker()
    _hosts(monkeypatch, dns="http://d:8016/")
    seen = _http(m, monkeypatch)
    monkeypatch.setattr(m, "collect_want", lambda: [])
    m.run_sweep()
    assert seen == ["http://d:8016/internal/records"]
    _hosts(monkeypatch)
    with pytest.raises(catalog.CatalogError):  # main() logs it and carries on, like any failed tick
        m.tick()
    assert seen == ["http://d:8016/internal/records"]
