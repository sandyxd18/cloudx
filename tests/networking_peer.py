"""The real networking app behind a requests adapter, for tests of services that call it.

Install `networking_peer(...)` after setting INTERNAL_TOKEN: every `requests.request` / `requests.get` to the networking
URL is handed to a TestClient of the real networking app (own sqlite database, real auth chain, ovn stubbed), so a
service under test and networking apply their rules to the same call. The headers a service sends reach networking as sent.
"""
import json

import requests
from fastapi.testclient import TestClient
from tests.stack import fresh_service

NET_URL = "http://networking.test"
OWNER = {"project_id": "p1", "role": "member", "roles": ["member"], "sub": "u-owner", "scope_type": "project"}
OTHER = {"project_id": "p2", "role": "member", "roles": ["member"], "sub": "u-other", "scope_type": "project"}
INTERNAL = {"sub": "internal", "username": "admin", "project_id": "admin", "role": "admin", "roles": ["admin"],
            "scope_type": "system", "jti": "internal"}


class Reply:
    def __init__(self, status_code, body):
        self.status_code, self._body = status_code, body
        self.text = json.dumps(body)
        self.content = self.text.encode()

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}", response=self)


def networking_peer(monkeypatch, tmp_path):
    monkeypatch.setenv("REVOCATION_CHECK", "false")
    monkeypatch.setenv("NETWORKING_API_URL", NET_URL)
    net = fresh_service("networking", monkeypatch, tmp_path)
    monkeypatch.setattr(net.ovn_cli, "ovn", lambda *a, **k: "")
    monkeypatch.setattr(net.events, "_rpc", type("Rpc", (), {"publish_event": lambda *a, **k: None})())
    m = net.models
    with net.db.SessionLocal() as db:
        db.add_all([
            m.Network(id="net-p1", name="p1-net", project_id="p1"),
            m.Network(id="net-shared", name="shared-net", project_id="p1", shared=True),
            m.Network(id="net-ext", name="ext-net", project_id="p1", external=True),
            m.Network(id="net-p2", name="p2-net", project_id="p2"),
            m.SecurityGroup(id="sg-p1", name="p1-sg", project_id="p1"),
            m.SecurityGroup(id="sg-p2", name="p2-sg", project_id="p2"),
            m.SecurityGroup(id="sg-default", name="default", project_id="default"),
        ])
        for i, network in enumerate(["net-p1", "net-shared", "net-ext", "net-p2"]):
            db.add(m.Subnet(id=f"sub-{network}", name=f"sub-{network}", network_id=network, cidr=f"10.{i}.0.0/24",
                            gateway=f"10.{i}.0.1", ip_version=4))
        db.commit()
    net_client = TestClient(net.app, raise_server_exceptions=False)
    seen = {"calls": []}

    def to_networking(method, url, headers, payload=None):
        seen["calls"].append((method, url[len(NET_URL):], headers or {}))
        r = net_client.request(method, url[len(NET_URL):], headers=headers, json=payload)
        return Reply(r.status_code, r.json() if r.content else {})

    real_request = requests.request

    def fake_request(method, url, headers=None, json=None, timeout=None, **kw):
        if url.startswith(NET_URL):
            return to_networking(method, url, headers, json)
        return real_request(method, url, headers=headers, json=json, timeout=timeout, **kw)

    def fake_get(url, headers=None, timeout=None, **kw):
        if url.startswith(NET_URL):
            return to_networking("GET", url, headers)
        assert "/internal/projects/" in url, url  # networking's quota check asks auth-api
        return Reply(200, {})

    monkeypatch.setattr(requests, "request", fake_request)
    monkeypatch.setattr(requests, "get", fake_get)
    return net, seen


def net_ports(net):
    with net.db.SessionLocal() as db:
        return {p.id: p for p in db.query(net.models.Port)}


def net_floating_ips(net):
    with net.db.SessionLocal() as db:
        return db.query(net.models.FloatingIP).count()
