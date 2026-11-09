"""A container's port is allocated by networking as the caller; a refusal is networking's own 404."""
import importlib

import pytest
from fastapi.testclient import TestClient
from shared.auth_middleware import require_auth, require_internal
from shared.internal import on_behalf_headers
from tests.networking_peer import INTERNAL, OTHER, net_ports, networking_peer

TOKEN = "s3cret"


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("INTERNAL_TOKEN", TOKEN)
    net, seen = networking_peer(monkeypatch, tmp_path)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'ct.sqlite'}")
    import services.container.app.main as m
    m = importlib.reload(m)
    m.Base.metadata.create_all(bind=m.engine)
    who = {}
    m.app.dependency_overrides[require_auth] = lambda: who
    m.app.dependency_overrides[require_internal] = lambda: None
    client = TestClient(m.app)
    client.post("/internal/ct-agent/node1/report", json={"containers": {}})
    client.get("/internal/ct-agent/node1")

    def as_(identity):
        who.clear()
        who.update(identity)

    return type("Env", (), {"m": m, "net": net, "seen": seen, "client": client, "as_": staticmethod(as_)})


def create(env, network="net-p2", **over):
    return env.client.post("/containers", json={"name": "web", "image": "nginx:1.27", "network_id": network, **over})


def rows(env):
    with env.m.SessionLocal() as db:
        return db.query(env.m.Container).count()


def assert_nothing_left(env):
    assert rows(env) == 0
    assert net_ports(env.net) == {}


def test_a_private_network_of_another_project_is_not_found(env):
    env.as_(OTHER)

    r = create(env, network="net-p1")

    assert r.status_code == 404 and r.json() == {"detail": "network not found"}
    assert_nothing_left(env)


def test_a_subnet_of_another_project_is_not_found(env):
    env.as_(OTHER)

    r = create(env, network="net-p2", subnet_id="sub-net-p1")

    assert r.status_code == 404, r.text
    assert_nothing_left(env)


def test_a_security_group_of_another_project_is_not_found(env):
    env.as_(OTHER)

    r = create(env, network="net-p2", security_group_ids=["sg-p1"])

    assert r.status_code == 404 and r.json() == {"detail": "security-group not found"}
    assert_nothing_left(env)


@pytest.mark.parametrize("network", ["net-p2", "net-shared", "net-ext"], ids=["own", "shared", "external"])
def test_own_shared_and_external_networks_work_and_the_port_belongs_to_the_caller(env, network):
    env.as_(OTHER)

    r = create(env, network=network)

    assert r.status_code == 201, r.text
    ports = list(net_ports(env.net).values())
    assert [p.project_id for p in ports] == ["p2"]
    assert rows(env) == 1


def test_an_internal_caller_allocates_on_any_network_without_an_identity(env):
    env.as_(INTERNAL)

    r = create(env, network="net-p1")

    assert r.status_code == 201, r.text
    for _, _, headers in env.seen["calls"]:
        assert "X-Caller-Claims" not in headers and headers["X-Internal-Token"] == TOKEN


def test_networking_is_asked_as_the_caller_for_the_port_only(env):
    env.as_(OTHER)

    assert create(env, network="net-p2").status_code == 201

    claims = on_behalf_headers(OTHER)["X-Caller-Claims"]
    for method, url, headers in env.seen["calls"]:
        if (method, url) == ("POST", "/ports"):
            assert headers["X-Caller-Claims"] == claims
        else:
            assert "X-Caller-Claims" not in headers, url


def test_a_caller_identity_that_does_not_fit_is_a_400(env):
    env.as_({**OTHER, "username": "x" * 3000})

    r = create(env, network="net-p2")

    assert r.status_code == 400 and r.json() == {"detail": "caller identity cannot be sent to a peer service"}
    assert_nothing_left(env)


def test_deleting_releases_the_port(env):
    env.as_(OTHER)
    c = create(env, network="net-p2").json()
    assert len(net_ports(env.net)) == 1

    assert env.client.delete(f"/containers/{c['id']}").status_code == 202
    env.client.post("/internal/ct-agent/node1/report", json={"containers": {c["id"]: {"state": "deleted"}}})

    assert net_ports(env.net) == {}
    assert rows(env) == 0


@pytest.mark.parametrize("network", ["net-p2", "net-shared"], ids=["own", "shared"])
def test_a_subnet_of_the_named_network_works(env, network):
    env.as_(OTHER)

    r = create(env, network=network, subnet_id=f"sub-{network}")

    assert r.status_code == 201, r.text
