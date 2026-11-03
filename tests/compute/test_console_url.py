"""console-url and remote-consoles hand the browser a link to the websockify on the server's own node, with a short-lived
token that names that node and the server's VNC port. Before, they returned a fixed noVNC page with `host=localhost`,
which only worked from the node itself."""
import logging
from urllib.parse import urlsplit

import pytest
from shared import console_token
from tests.compute import test_catalog_failures

api, _server = test_catalog_failures.api, test_catalog_failures._server

VNC = {"status": "available", "listen": "127.0.0.1", "port": 5901, "host": "node-1", "proxy_host": "console.node-1.test"}


@pytest.fixture(autouse=True)
def secret(monkeypatch):
    monkeypatch.setenv("AUTH_SECRET_KEY", "test-secret")


def _worker_says(svc, monkeypatch, info):
    monkeypatch.setattr(svc.clients, "call_worker", lambda method, payload, timeout=None: dict(info))


def _token_of(url):
    query = urlsplit(url).query
    assert query.startswith("autoconnect=1&path=websockify%3Ftoken="), query
    return query.split("token=", 1)[1]


def test_console_url_contains_a_token_for_the_servers_host_and_port(api, monkeypatch):
    svc, client, _, _ = api
    _server(svc, "ACTIVE")
    _worker_says(svc, monkeypatch, VNC)

    body = client.get("/servers/srv1/console-url").json()

    parts = urlsplit(body["novnc_url"])
    assert (parts.scheme, parts.hostname, parts.port, parts.path) == ("http", "console.node-1.test", 6080, "/vnc.html")
    assert console_token.verify_token(_token_of(body["novnc_url"]), "node-1") == 5901
    assert body["vnc"]["port"] == 5901


def test_console_url_without_vnc_port_has_no_novnc_url(api, monkeypatch):
    svc, client, _, _ = api
    _server(svc, "ACTIVE")
    _worker_says(svc, monkeypatch, {"status": "auto-assigned", "port": None, "host": "node-1", "proxy_host": "node-1"})

    r = client.get("/servers/srv1/console-url")

    assert r.status_code == 200 and r.json()["novnc_url"] is None


def test_console_url_when_the_worker_is_unreachable_has_no_novnc_url(api, monkeypatch):
    svc, client, _, _ = api
    _server(svc, "ACTIVE")

    def down(*a, **k):
        raise RuntimeError("no reply")

    monkeypatch.setattr(svc.clients, "call_worker", down)

    r = client.get("/servers/srv1/console-url")

    assert r.status_code == 200 and r.json()["novnc_url"] is None


def test_console_url_without_a_signing_secret_has_no_novnc_url_and_does_not_fail(api, monkeypatch, caplog):
    svc, client, _, _ = api
    _server(svc, "ACTIVE")
    _worker_says(svc, monkeypatch, VNC)
    monkeypatch.delenv("AUTH_SECRET_KEY")

    with caplog.at_level(logging.WARNING):
        r = client.get("/servers/srv1/console-url")

    assert r.status_code == 200 and r.json()["novnc_url"] is None
    assert "AUTH_SECRET_KEY" in caplog.text


def test_remote_consoles_uses_the_same_url(api, monkeypatch):
    svc, client, _, _ = api
    _server(svc, "ACTIVE")
    _worker_says(svc, monkeypatch, VNC)

    url = client.post("/servers/srv1/remote-consoles", json={}).json()["remote_console"]["url"]

    parts = urlsplit(url)
    assert (parts.scheme, parts.hostname, parts.port, parts.path) == ("http", "console.node-1.test", 6080, "/vnc.html")
    assert console_token.verify_token(_token_of(url), "node-1") == 5901


def test_remote_consoles_without_vnc_port_has_no_url(api, monkeypatch):
    svc, client, _, _ = api
    _server(svc, "ACTIVE")
    _worker_says(svc, monkeypatch, {"status": "auto-assigned", "port": None})

    r = client.post("/servers/srv1/remote-consoles", json={})

    assert r.status_code == 200 and r.json()["remote_console"]["url"] is None


def test_token_issued_for_node_a_is_refused_for_node_b(api, monkeypatch):
    svc, client, _, _ = api
    _server(svc, "ACTIVE")
    _worker_says(svc, monkeypatch, VNC)

    url = client.get("/servers/srv1/console-url").json()["novnc_url"]

    with pytest.raises(ValueError):
        console_token.verify_token(_token_of(url), "node-b")
