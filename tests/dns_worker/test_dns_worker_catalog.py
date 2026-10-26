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
    monkeypatch.setattr(m, "collect_want", lambda unavailable=None: [])
    m.run_sweep()
    assert seen == ["http://d:8016/internal/records"]
    _hosts(monkeypatch)
    with pytest.raises(catalog.CatalogError):  # main() logs it and carries on, like any failed tick
        m.tick()
    assert seen == ["http://d:8016/internal/records"]


def _fips_and_servers(m, monkeypatch, lb_error):
    """Networking and compute answer; the load balancer API fails with `lb_error`."""
    def get(url, **kw):
        if "load-balancers" in url:
            raise lb_error
        body = {"projects": {"p1": "demo"}} if url.endswith("/internal/projects") else (
            [{"id": "f1", "floating_ip_address": "192.168.15.100", "project_id": "p1"}] if url.endswith("/floating-ips") else
            [{"id": "s1", "name": "vm", "project_id": "p1", "ip_address": "10.10.0.2", "status": "ACTIVE"}])
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: body)

    monkeypatch.setattr(m.requests, "get", get)


def test_a_load_balancer_api_that_is_not_installed_does_not_stop_the_sweep(monkeypatch):
    # a trimmed install has no load-balancer-api: the catalog still lists it with its default address, which refuses
    m = worker()
    _hosts(monkeypatch, network="http://n:8001", compute="http://c:8003", **{"load-balancer": "http://127.0.0.1:8008"})
    _fips_and_servers(m, monkeypatch, m.requests.ConnectionError("refused"))
    unavailable = set()
    want = m.collect_want(unavailable)
    assert [w["owner"] for w in want] == ["floating_ip:f1", "server:s1"]
    assert unavailable == {"load_balancer", "load_balancer_internal"}


def test_a_load_balancer_missing_from_the_catalog_is_skipped_too(monkeypatch):
    m = worker()
    _hosts(monkeypatch, network="http://n:8001", compute="http://c:8003")
    _fips_and_servers(m, monkeypatch, AssertionError("must not be called"))
    unavailable = set()
    assert [w["owner"] for w in m.collect_want(unavailable)] == ["floating_ip:f1", "server:s1"]
    assert unavailable == {"load_balancer", "load_balancer_internal"}


def test_run_sweep_registers_what_it_can_and_deletes_nothing_it_could_not_check(monkeypatch):
    m = worker()
    _hosts(monkeypatch, dns="http://d:8016", network="http://n:8001", compute="http://c:8003")
    _fips_and_servers(m, monkeypatch, AssertionError("must not be called"))
    posted, deleted = [], []
    original_get = m.requests.get

    def get(url, **kw):
        if url.endswith("/internal/records"):
            return SimpleNamespace(json=lambda: {"records": {"load_balancer:9": {"fqdn": "lb.elb.cloud.lab", "records": ["1.1.1.1"]}}})
        return original_get(url, **kw)

    monkeypatch.setattr(m.requests, "get", get)
    monkeypatch.setattr(m.requests, "post", lambda url, **kw: posted.append(kw["json"]["owner"]) or SimpleNamespace(status_code=201))
    monkeypatch.setattr(m.requests, "delete", lambda url, **kw: deleted.append(kw["params"]["owner"]))
    m.run_sweep()
    assert posted == ["floating_ip:f1", "server:s1"]
    assert deleted == []  # load_balancer:9 stays: the load balancer API was not asked
