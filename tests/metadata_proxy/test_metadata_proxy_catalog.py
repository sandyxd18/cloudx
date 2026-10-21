import pytest
import requests
from fastapi.testclient import TestClient
from shared import catalog

import services.metadata_proxy.app.main as proxy

PEERS = ("METADATA_API_URL", "NETWORKING_API_URL")


class Resp:
    def __init__(self, data=None, content=b"ok"):
        self.status_code, self._d, self.content = 200, data or {}, content
        self.headers = {"content-type": "text/plain"}

    def raise_for_status(self):
        pass

    def json(self):
        return self._d


@pytest.fixture
def client(monkeypatch):
    for key in PEERS:
        monkeypatch.delenv(key, raising=False)
    return TestClient(proxy.app)


def _record(monkeypatch):
    seen = []

    def get(url, **kw):
        seen.append(url)
        return Resp({"items": [{"fixed_ip": "10.0.0.5", "id": "p1", "device_id": "i1"}]})

    monkeypatch.setattr(proxy.requests, "get", get)
    return seen


def test_requests_go_to_the_catalog_hosts(client, monkeypatch):
    asked = []
    hosts = {"metadata": "http://md-host:8775/", "network": "http://net-host:9696/"}
    monkeypatch.setattr(catalog, "url", lambda service, interface="internal": asked.append(service) or hosts[service])
    seen = _record(monkeypatch)
    r = client.get("/latest/meta-data/", headers={"X-Forwarded-For": "10.0.0.5"})
    assert r.status_code == 200
    assert seen == ["http://net-host:9696/ports", "http://md-host:8775/latest/meta-data/"]  # no double slash
    assert sorted(asked) == ["metadata", "network"]


def test_the_override_wins_over_the_catalog(client, monkeypatch):
    monkeypatch.setenv("METADATA_API_URL", "http://override-md:1/")
    monkeypatch.setenv("NETWORKING_API_URL", "http://override-net:2")
    monkeypatch.setattr(catalog, "_catalog", lambda service: pytest.fail("the override must not consult the catalog"))
    seen = _record(monkeypatch)
    assert client.get("/latest/").status_code == 200
    assert seen[-1] == "http://override-md:1/latest/"


def test_a_catalog_error_for_networking_is_handled_like_an_unreachable_networking_api(client, monkeypatch):
    def partial(service, interface="internal"):
        if service == "network":
            raise catalog.CatalogError("cannot resolve service 'network'")
        return "http://md-host:8775"

    monkeypatch.setattr(catalog, "url", partial)
    seen = _record(monkeypatch)
    r = client.get("/latest/meta-data/", headers={"X-Forwarded-For": "10.0.0.5"})
    assert r.status_code == 200 and seen == ["http://md-host:8775/latest/meta-data/"]  # lookup_port gave {} as before


def test_a_catalog_error_for_metadata_is_a_502_like_an_unreachable_upstream(client, monkeypatch):
    def partial(service, interface="internal"):
        if service == "metadata":
            raise catalog.CatalogError("cannot resolve service 'metadata'")
        return "http://net-host:9696"

    monkeypatch.setattr(catalog, "url", partial)
    _record(monkeypatch)
    r = client.get("/latest/")
    assert r.status_code == 502 and "cannot resolve service 'metadata'" in r.json()["detail"]


def test_an_unreachable_upstream_is_still_a_502(client, monkeypatch):
    monkeypatch.setenv("METADATA_API_URL", "http://md:1")
    monkeypatch.setenv("NETWORKING_API_URL", "http://net:1")

    def boom(url, **kw):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(proxy.requests, "get", boom)
    assert client.get("/latest/").status_code == 502
