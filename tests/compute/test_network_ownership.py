"""Networks, ports and security groups named by a user are resolved by networking as that user.

compute's real `clients.api_get` / `api_post` / `api_delete` build the headers (internal token + X-Caller-Claims);
only `requests` is replaced, by an adapter that hands each networking URL to a real networking TestClient (ovn
stubbed). So compute and networking are tested together: a refusal is networking's own 404, passed through.
"""
import json

import pytest
import requests
from fastapi.testclient import TestClient
from shared.auth_middleware import require_auth
from shared.internal import internal_headers, on_behalf_headers
from tests.stack import fresh_service

TOKEN = "s3cret"
NET_URL = "http://networking.test"
OWNER = {"project_id": "p1", "role": "member", "roles": ["member"], "sub": "u-owner", "scope_type": "project"}
OTHER = {"project_id": "p2", "role": "member", "roles": ["member"], "sub": "u-other", "scope_type": "project"}
DOMAIN_ADMIN = {"project_id": "p9", "role": "member", "roles": ["member"], "sub": "u-da", "scope_type": "project",
                "domain_admin": True, "domain_id": "d1"}
OTHER_DOMAIN_ADMIN = {**DOMAIN_ADMIN, "sub": "u-da2", "domain_id": "d2"}
INTERNAL = {"sub": "internal", "username": "admin", "project_id": "admin", "role": "admin", "roles": ["admin"],
            "scope_type": "system", "jti": "internal"}
VOLUME = {"id": "vol1", "name": "data", "status": "available", "bootable": True, "project_id": None, "image_id": "img0"}


class Reply:
    def __init__(self, status_code, body):
        self.status_code, self._body = status_code, body
        self.text = json.dumps(body)

    def json(self):
        return self._body

    def raise_for_status(self):
        assert self.status_code < 400


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("INTERNAL_TOKEN", TOKEN)
    monkeypatch.setenv("REVOCATION_CHECK", "false")
    for key in ("NETWORKING_API_URL", "BLOCK_STORE_API_URL", "PLACEMENT_API_URL"):
        monkeypatch.setenv(key, f"http://{key.split('_')[0].lower()}.test")
    monkeypatch.setattr("shared.domain.domain_projects", lambda d: ["p1"] if d == "d1" else [])

    # the real networking-api, on its own database, with the real auth chain and ovn stubbed
    net = fresh_service("networking", monkeypatch, tmp_path)
    monkeypatch.setattr(net.ovn_cli, "ovn", lambda *a, **k: "")
    monkeypatch.setattr(net.events, "_rpc", type("Rpc", (), {"publish_event": lambda *a, **k: None})())
    m = net.models
    with net.db.SessionLocal() as db:
        db.add_all([
            m.Network(id="net-p1", name="p1-net", project_id="p1"),
            m.Network(id="net-p1b", name="p1-net-b", project_id="p1"),
            m.Network(id="net-shared", name="shared-net", project_id="p1", shared=True),
            m.Network(id="net-ext", name="ext-net", project_id="p1", external=True),
            m.Network(id="net-p2", name="p2-net", project_id="p2"),
            m.Network(id="net-p2b", name="p2-net-b", project_id="p2"),
            m.SecurityGroup(id="sg-p1", name="p1-sg", project_id="p1"),
            m.SecurityGroup(id="sg-p2", name="p2-sg", project_id="p2"),
            m.SecurityGroup(id="sg-default", name="default", project_id="default"),
        ])
        for i, network in enumerate(["net-p1", "net-p1b", "net-shared", "net-ext", "net-p2", "net-p2b"]):
            db.add(m.Subnet(id=f"sub-{network}", name=f"sub-{network}", network_id=network, cidr=f"10.{i}.0.0/24",
                            gateway=f"10.{i}.0.1", ip_version=4))
        db.add_all([
            m.Subnet(id="sub6-net-p2", name="sub6-net-p2", network_id="net-p2", cidr="fd00:2::/64", gateway="fd00:2::1", ip_version=6),
            m.Subnet(id="sub6-net-p1", name="sub6-net-p1", network_id="net-p1", cidr="fd00:1::/64", gateway="fd00:1::1", ip_version=6),
            m.Port(id="port-p1", name="p1-port", network_id="net-p1", mac_address="02:00:00:00:00:01", project_id="p1"),
            m.Port(id="port-free", name="p2-free", network_id="net-p2", mac_address="02:00:00:00:00:02", project_id="p2"),
            m.Port(id="port-used", name="p2-used", network_id="net-p2", mac_address="02:00:00:00:00:03", project_id="p2",
                   device_id="other-server"),
            m.Port(id="port-mine", name="p2-mine", network_id="net-p2", mac_address="02:00:00:00:00:04", project_id="p2",
                   device_id="srv1"),
        ])
        db.commit()
    net_client = TestClient(net.app, raise_server_exceptions=False)

    # compute, on its own database
    svc = fresh_service("compute", monkeypatch, tmp_path)
    who = {}
    svc.app.dependency_overrides[require_auth] = lambda: who
    seen = {"claims": [], "released": [], "casts": [], "net_calls": [], "quota_projects": [], "block_deletes": []}

    def to_networking(method, url, headers, payload=None):
        seen["net_calls"].append((method, url[len(NET_URL):], headers or {}))
        r = net_client.request(method, url[len(NET_URL):], headers=headers, json=payload)
        return Reply(r.status_code, r.json())

    def fake_get(url, headers=None, timeout=None, **kw):
        if url.startswith(NET_URL):
            return to_networking("GET", url, headers)
        if "/internal/projects/" in url:  # networking's quota check asks auth-api
            seen["quota_projects"].append(url.split("/internal/projects/")[1].split("/")[0])
            return Reply(200, {})
        raise AssertionError(f"unexpected GET {url}")

    def fake_post(url, headers=None, json=None, timeout=None, **kw):
        if url.startswith(NET_URL):
            return to_networking("POST", url, headers, json)
        assert url.endswith("/internal/claim"), url  # placement claim
        seen["claims"].append(json["consumer_uuid"])
        return Reply(200, {"host": "node-1"})

    def fake_delete(url, headers=None, json=None, timeout=None, **kw):
        if url.startswith(NET_URL):
            return to_networking("DELETE", url, headers, json)
        assert "/internal/allocations/" in url, url  # placement release
        seen["released"].append(url.rsplit("/", 1)[1])
        return Reply(200, {})

    from importlib import import_module

    servers = import_module("services.compute.app.routers.servers")
    monkeypatch.setattr(servers, "_check_project_quota", lambda *a, **k: None)  # asks auth-api, not what is tested here
    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(requests, "delete", fake_delete)
    monkeypatch.setattr(svc.clients, "block_get", lambda path: dict(VOLUME))
    monkeypatch.setattr(svc.clients, "block_delete", lambda path: seen["block_deletes"].append(path))
    monkeypatch.setattr(svc.clients, "cast_to_worker", lambda method, payload: seen["casts"].append(method))
    monkeypatch.setattr(svc.clients, "_rpc", type("Rpc", (), {"publish_event": lambda *a, **k: None})())
    with svc.SessionLocal() as db:
        db.add(svc.Flavor(name="small", vcpus=1, ram_mb=512, disk_gb=1))
        db.commit()

    def as_(identity):
        who.clear()
        who.update(identity)

    return type("Env", (), {"svc": svc, "net": net, "client": TestClient(svc.app), "seen": seen,
                            "as_": staticmethod(as_)})


def create(env, network="net-p2", **over):
    return env.client.post("/servers", json={"name": "vm1", "flavor": "small", "network": network,
                                             "boot_volume": "vol1", **over})


def server_rows(env):
    with env.svc.SessionLocal() as db:
        return db.query(env.svc.Server).count()


def networking_ports(env):
    with env.net.db.SessionLocal() as db:
        return {p.id: p for p in db.query(env.net.models.Port).filter_by(device_owner="compute:instance")}


def assert_nothing_was_created(env):
    seen = env.seen
    assert seen["released"] == seen["claims"], "a placement claim made before the refusal is released"
    assert server_rows(env) == 0
    assert networking_ports(env) == {}, "no port of the refused request is left in networking"
    assert seen["casts"] == [] and seen["block_deletes"] == []


# ── create_server: networks ──────────────────────────────────────────────────

@pytest.mark.parametrize("ref,detail", [("net-p1", "network not found"), ("p1-net", "network 'p1-net' not found")],
                         ids=["by_id", "by_name"])
def test_create_server_refuses_a_private_network_of_another_project(env, ref, detail):
    env.as_(OTHER)

    r = create(env, network=ref)

    assert r.status_code == 404 and r.json() == {"detail": detail}
    assert_nothing_was_created(env)


@pytest.mark.parametrize("extra", ["net-p1", "p1-net"], ids=["by_id", "by_name"])
def test_create_server_refuses_an_extra_network_of_another_project(env, extra):
    env.as_(OTHER)

    r = create(env, network="net-p2", networks=["net-p2b", extra])

    assert r.status_code == 404 and "not found" in r.json()["detail"]
    assert len(env.seen["claims"]) == 1, "the extra networks are looked up after the placement claim"
    assert_nothing_was_created(env)
    deleted = [url for method, url, _ in env.seen["net_calls"] if method == "DELETE"]
    assert len(deleted) == 2, "the ports made for the earlier networks are removed again"


def test_create_server_refuses_an_extra_network_of_another_project_with_ipv6(env):
    env.as_(OTHER)

    r = create(env, network="net-p2", networks=["net-p1"], ipv6=True)

    assert r.status_code == 404 and "not found" in r.json()["detail"]
    assert_nothing_was_created(env)


@pytest.mark.parametrize("network", ["net-p2", "net-shared", "net-ext"], ids=["own", "shared", "external"])
def test_create_server_accepts_own_shared_and_external_networks(env, network):
    env.as_(OTHER)

    r = create(env, network=network)

    assert r.status_code == 201, r.text
    assert r.json()["network_id"] == network
    ports = networking_ports(env)
    assert len(ports) == 1 and next(iter(ports.values())).project_id == "p2", "the port belongs to the member's project"
    assert env.seen["casts"] == ["build_instance"]


def test_create_server_accepts_extra_networks_the_caller_can_use(env):
    env.as_(OTHER)

    r = create(env, network="net-p2", networks=["net-shared", "net-p2b"])

    assert r.status_code == 201, r.text
    assert len(networking_ports(env)) == 3


def test_a_domain_admin_can_use_a_network_of_a_project_of_their_domain(env):
    env.as_(DOMAIN_ADMIN)

    r = create(env, network="net-p1")

    assert r.status_code == 201, r.text
    assert next(iter(networking_ports(env).values())).project_id == "p9"


def test_a_domain_admin_of_another_domain_is_refused(env):
    env.as_(OTHER_DOMAIN_ADMIN)

    r = create(env, network="net-p1")

    assert r.status_code == 404 and r.json() == {"detail": "network not found"}
    assert_nothing_was_created(env)


def test_the_internal_caller_can_use_any_network_and_sends_no_identity(env):
    env.as_(INTERNAL)

    r = create(env, network="net-p1")

    assert r.status_code == 201, r.text
    assert env.seen["net_calls"], "networking was reached"
    for _, _, headers in env.seen["net_calls"]:
        assert "X-Caller-Claims" not in headers and headers["X-Internal-Token"] == TOKEN


def test_networking_is_asked_as_the_caller(env):
    env.as_(OTHER)

    assert create(env, network="net-p2", networks=["net-p2b"]).status_code == 201

    for method, url, headers in env.seen["net_calls"]:
        if method == "GET" and url.startswith("/networks/"):
            assert headers["X-Caller-Claims"] == on_behalf_headers(OTHER)["X-Caller-Claims"], url
        if method == "POST" and url == "/ports":
            assert headers["X-Caller-Claims"] == on_behalf_headers(OTHER)["X-Caller-Claims"]
        if method == "GET" and url.startswith("/subnets/"):
            assert "X-Caller-Claims" not in headers, "ids from the database are looked up as the service"


def test_the_port_of_a_new_server_counts_toward_the_callers_project(env):
    env.as_(OTHER)

    assert create(env, network="net-p2").status_code == 201

    assert env.seen["quota_projects"] == ["p2"]


# ── create_server: security groups ───────────────────────────────────────────

@pytest.mark.parametrize("group", ["sg-p1", "p1-sg"], ids=["by_id", "by_name"])
def test_create_server_refuses_a_security_group_of_another_project(env, group):
    env.as_(OTHER)

    r = create(env, network="net-p2", security_groups=[group])

    assert r.status_code == 404
    assert r.json() == {"detail": "security-group not found" if group == "sg-p1" else "security-group 'p1-sg' not found"}
    assert len(env.seen["claims"]) == 1
    assert_nothing_was_created(env)


def test_create_server_accepts_a_security_group_of_the_callers_project(env):
    env.as_(OTHER)

    r = create(env, network="net-p2", security_groups=["sg-p2"])

    assert r.status_code == 201, r.text
    assert next(iter(networking_ports(env).values())).security_group_ids == "sg-p2"


def test_create_server_without_security_groups_uses_the_default_group(env):
    env.as_(OTHER)

    r = create(env, network="net-p2")

    assert r.status_code == 201, r.text
    assert next(iter(networking_ports(env).values())).security_group_ids == "sg-default"


# ── clients: the header ──────────────────────────────────────────────────────

def test_api_post_sends_the_callers_identity_only_when_given_one(env, monkeypatch):
    sent = []

    def fake(url, json=None, headers=None, timeout=None, **kw):
        sent.append(headers)
        return Reply(200, {})

    monkeypatch.setattr(requests, "post", fake)

    env.svc.clients.api_post("http://peer.test/x", {})
    env.svc.clients.api_post("http://peer.test/x", {}, caller=OTHER)
    env.svc.clients.api_post("http://peer.test/x", {}, caller=INTERNAL)

    assert sent[0] == {"Content-Type": "application/json", **internal_headers()}
    assert sent[1] == {"Content-Type": "application/json", **on_behalf_headers(OTHER)}
    assert sent[2] == {"Content-Type": "application/json", **internal_headers()}


@pytest.mark.parametrize("call", ["get", "post"])
def test_claims_too_large_for_the_header_are_refused_before_the_call(env, monkeypatch, call):
    monkeypatch.setattr(requests, "get", lambda *a, **k: pytest.fail("no call is made"))
    monkeypatch.setattr(requests, "post", lambda *a, **k: pytest.fail("no call is made"))
    big = {**OTHER, "roles": [f"role-{i:04d}" for i in range(400)]}

    with pytest.raises(env.svc.clients.HTTPException) as caught:
        if call == "get":
            env.svc.clients.api_get("http://peer.test/x", caller=big)
        else:
            env.svc.clients.api_post("http://peer.test/x", {}, caller=big)

    assert caught.value.status_code == 400
    assert caught.value.detail == "caller identity cannot be sent to a peer service"


def test_create_server_with_claims_too_large_for_the_header_is_400_and_creates_nothing(env):
    env.as_({**OTHER, "roles": [f"role-{i:04d}" for i in range(400)]})

    r = create(env, network="net-p2")

    assert r.status_code == 400 and r.json() == {"detail": "caller identity cannot be sent to a peer service"}
    assert_nothing_was_created(env)


# ── add_server_port ──────────────────────────────────────────────────────────

def add_server(env, project="p2"):
    with env.svc.SessionLocal() as db:
        db.add(env.svc.Server(id="srv1", name="vm-existing", project_id=project, image_id="img0", root_volume_id="vol1",
                              flavor="small", network_id="net-p2", port_id="port-main", host="node-1", status="ACTIVE"))
        db.commit()


def extra_ports(env):
    with env.svc.SessionLocal() as db:
        return db.get(env.svc.Server, "srv1").extra_port_ids


@pytest.mark.parametrize("ref,detail", [("port-p1", "port not found"), ("p1-port", "port 'p1-port' not found")],
                         ids=["by_id", "by_name"])
def test_add_server_port_refuses_a_port_of_another_project(env, ref, detail):
    add_server(env)
    env.as_(OTHER)

    r = env.client.post("/servers/srv1/ports", json={"port": ref})

    assert r.status_code == 404 and r.json() == {"detail": detail}
    assert not extra_ports(env) and env.seen["casts"] == []


@pytest.mark.parametrize("ref", ["port-used", "p2-used"], ids=["by_id", "by_name"])
def test_add_server_port_refuses_a_port_another_server_uses(env, ref):
    add_server(env)
    env.as_(OTHER)

    r = env.client.post("/servers/srv1/ports", json={"port": ref})

    assert r.status_code == 409 and r.json() == {"detail": "port is in use"}
    assert not extra_ports(env) and env.seen["casts"] == []


@pytest.mark.parametrize("ref", ["port-free", "port-mine"], ids=["free", "already_this_servers"])
def test_add_server_port_accepts_a_free_port_of_the_callers_project(env, ref):
    add_server(env)
    env.as_(OTHER)

    r = env.client.post("/servers/srv1/ports", json={"port": ref})

    assert r.status_code == 200, r.text
    assert r.json()["port_id"] == ref
    assert extra_ports(env) == ref and env.seen["casts"] == ["attach_port"]


def test_add_server_port_asks_networking_as_the_caller(env):
    add_server(env)
    env.as_(OTHER)

    env.client.post("/servers/srv1/ports", json={"port": "port-free"})

    (_, url, headers), = env.seen["net_calls"]
    assert url == "/ports/port-free"
    assert headers["X-Caller-Claims"] == on_behalf_headers(OTHER)["X-Caller-Claims"]


def test_add_server_port_refuses_a_port_another_live_server_lists_as_an_extra_port(env):
    add_server(env)
    with env.svc.SessionLocal() as db:
        db.add(env.svc.Server(id="srv2", name="vm-other", project_id="p2", image_id="img0", root_volume_id="vol2",
                              flavor="small", network_id="net-p2", port_id="port-main2", extra_port_ids="port-free",
                              host="node-1", status="ACTIVE"))
        db.commit()
    env.as_(OTHER)

    r = env.client.post("/servers/srv1/ports", json={"port": "port-free"})

    assert r.status_code == 409 and r.json() == {"detail": "port is in use"}
    assert not extra_ports(env) and env.seen["casts"] == []


def test_add_server_port_accepts_a_port_a_deleted_server_listed(env):
    add_server(env)
    with env.svc.SessionLocal() as db:
        db.add(env.svc.Server(id="srv2", name="vm-gone", project_id="p2", image_id="img0", root_volume_id="vol2",
                              flavor="small", network_id="net-p2", port_id="port-main2", extra_port_ids="port-free",
                              host="node-1", status="DELETED"))
        db.commit()
    env.as_(OTHER)

    assert env.client.post("/servers/srv1/ports", json={"port": "port-free"}).status_code == 200
