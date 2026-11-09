"""The proxy tells the metadata service who the VM is from its source address and port, not from the VM's query string."""
import pytest
from fastapi.testclient import TestClient

import services.metadata_proxy.app.main as proxy

TOKEN = "s3cret"


class Resp:
    status_code = 200
    content = b"ok"
    headers = {"content-type": "text/plain"}

    def __init__(self, data=None):
        self._d = data or {}

    def raise_for_status(self):
        pass

    def json(self):
        return self._d


@pytest.fixture
def calls(monkeypatch):
    monkeypatch.setenv("INTERNAL_TOKEN", TOKEN)
    monkeypatch.setenv("METADATA_API_URL", "http://md:1")
    monkeypatch.setenv("NETWORKING_API_URL", "http://net:2")
    seen = []

    def get(url, **kw):
        seen.append((url, kw))
        return Resp({"items": [{"fixed_ip": "10.0.0.5", "id": "p1", "device_id": "i1"}]})

    monkeypatch.setattr(proxy.requests, "get", get)
    return seen


def test_the_query_string_of_the_vm_is_not_forwarded(calls):
    client = TestClient(proxy.app, client=("127.0.0.1", 50000))

    r = client.get("/latest/meta-data/?instance_id=other-instance&fixed_ip=10.0.0.9&port_id=other",
                   headers={"X-Forwarded-For": "10.0.0.5"})

    assert r.status_code == 200
    url, kw = calls[-1]
    assert url == "http://md:1/latest/meta-data/"
    assert not kw.get("params")


def test_the_identity_comes_from_the_port_of_the_source_address(calls):
    client = TestClient(proxy.app, client=("127.0.0.1", 50000))

    client.get("/latest/user-data?instance_id=other-instance", headers={"X-Forwarded-For": "10.0.0.5"})

    headers = calls[-1][1]["headers"]
    assert headers["X-Instance-ID"] == "i1" and headers["X-Port-ID"] == "p1"
    assert headers["X-Internal-Token"] == TOKEN


def test_the_port_lookup_carries_the_internal_token(calls):
    client = TestClient(proxy.app, client=("127.0.0.1", 50000))

    client.get("/latest/", headers={"X-Forwarded-For": "10.0.0.5"})

    url, kw = calls[0]
    assert url == "http://net:2/ports"
    assert kw["headers"] == {"X-Internal-Token": TOKEN}


def test_identity_headers_sent_by_the_vm_are_not_forwarded(calls):
    client = TestClient(proxy.app, client=("127.0.0.1", 50000))

    client.get("/latest/", headers={"X-Forwarded-For": "10.0.0.77", "X-Instance-ID": "other-instance", "X-Port-ID": "other"})

    headers = calls[-1][1]["headers"]
    assert "X-Instance-ID" not in headers and "X-Port-ID" not in headers


@pytest.mark.parametrize("path", ["latest/meta-data/%3Finstance_id=other", "latest/x%23y", "latest/%3Ffixed_ip=10.0.0.9"])
def test_characters_of_the_path_never_become_a_query_or_fragment(calls, path):
    client = TestClient(proxy.app, client=("127.0.0.1", 50000))

    client.get(f"/{path}", headers={"X-Forwarded-For": "10.0.0.5"})

    upstream = [url for url, _ in calls if url.startswith("http://md:1")]
    assert upstream and all("?" not in url and "#" not in url for url in upstream)


@pytest.mark.parametrize("path", ["latest/../servers", "latest/%2e%2e/x", "openstack/%2E%2E", "latest/%2e/x", "latest%5Cx"])
def test_dot_segments_and_backslashes_are_not_found(calls, path):
    client = TestClient(proxy.app, client=("127.0.0.1", 50000))

    r = client.get(f"/{path}", headers={"X-Forwarded-For": "10.0.0.5"})

    assert r.status_code == 404
    assert not [url for url, _ in calls if url.startswith("http://md:1")]
