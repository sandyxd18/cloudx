"""A load balancer's VIP port and floating IP are allocated by networking as the caller; a refusal is networking's own 404."""
import importlib

import pytest
from fastapi.testclient import TestClient
from shared.auth_middleware import require_auth
from shared.internal import on_behalf_headers
from tests.networking_peer import INTERNAL, OTHER, net_floating_ips, net_ports, networking_peer

TOKEN = "s3cret"


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("INTERNAL_TOKEN", TOKEN)
    net, seen = networking_peer(monkeypatch, tmp_path)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'lb.sqlite'}")
    import services.load_balancer.app.main as m
    m = importlib.reload(m)
    m.Base.metadata.create_all(bind=m.engine)
    monkeypatch.setattr(m, "_apply_lb_state", lambda db, lb: None)
    monkeypatch.setattr(m, "_get_driver", lambda lb: type("Driver", (), {"delete": lambda self, d: None})())
    monkeypatch.setattr(m.dns_names, "register", lambda *a, **k: None)
    monkeypatch.setattr(m.dns_names, "unregister", lambda *a, **k: None, raising=False)
    who = {}
    m.app.dependency_overrides[require_auth] = lambda: who
    client = TestClient(m.app, raise_server_exceptions=False)

    def as_(identity):
        who.clear()
        who.update(identity)

    return type("Env", (), {"m": m, "net": net, "seen": seen, "client": client, "as_": staticmethod(as_)})


def create(env, network="net-p2", **over):
    return env.client.post("/load-balancers", json={"name": "web", "lb_type": "NLB", "vip_network_id": network, **over})


def rows(env):
    with env.m.SessionLocal() as db:
        return db.query(env.m.LoadBalancer).count()


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

    r = create(env, network="net-p2", vip_subnet_id="sub-net-p1")

    assert r.status_code == 404, r.text
    assert_nothing_left(env)


@pytest.mark.parametrize("network", ["net-p2", "net-shared", "net-ext"], ids=["own", "shared", "external"])
def test_own_shared_and_external_networks_work_and_the_vip_port_belongs_to_the_caller(env, network):
    env.as_(OTHER)

    r = create(env, network=network, vip_subnet_id=f"sub-{network}")

    assert r.status_code == 201, r.text
    assert [p.project_id for p in net_ports(env.net).values()] == ["p2"]
    assert rows(env) == 1


def test_an_internal_caller_allocates_on_any_network_without_an_identity(env):
    env.as_(INTERNAL)

    r = create(env, network="net-p1", floating_network="net-ext")

    assert r.status_code == 201, r.text
    assert env.seen["calls"]
    for _, _, headers in env.seen["calls"]:
        assert "X-Caller-Claims" not in headers and headers["X-Internal-Token"] == TOKEN


def test_networking_is_asked_as_the_caller_for_the_port_and_the_floating_ip_only(env):
    env.as_(OTHER)

    assert create(env, network="net-p2", floating_network="net-ext").status_code == 201

    claims = on_behalf_headers(OTHER)["X-Caller-Claims"]
    as_caller = [(m, u) for m, u, h in env.seen["calls"] if h.get("X-Caller-Claims") == claims]
    assert sorted(as_caller) == [("POST", "/floating-ips"), ("POST", "/ports")]
    for method, url, headers in env.seen["calls"]:
        if (method, url) not in as_caller:
            assert "X-Caller-Claims" not in headers, url


def test_the_floating_ip_belongs_to_the_callers_project(env):
    env.as_(OTHER)

    r = create(env, network="net-p2", floating_network="net-ext")

    assert r.status_code == 201, r.text
    assert net_floating_ips(env.net) == 1
    with env.net.db.SessionLocal() as db:
        assert [f.project_id for f in db.query(env.net.models.FloatingIP)] == ["p2"]


def test_a_floating_network_of_another_project_is_refused_and_the_load_balancer_keeps_its_error_state(env):
    env.as_(OTHER)

    r = create(env, network="net-p2", floating_network="net-p1")

    assert r.status_code == 201 and r.json()["provisioning_status"] == "ERROR"
    assert r.json()["vip_floating_ip"] is None
    assert net_floating_ips(env.net) == 0
    assert len(net_ports(env.net)) == 1  # the VIP port stays with the LB until it is deleted, as before

    assert env.client.delete(f"/load-balancers/{r.json()['id']}").status_code in (200, 202, 204)
    assert net_ports(env.net) == {}


def test_a_caller_identity_that_does_not_fit_is_a_400(env):
    env.as_({**OTHER, "username": "x" * 3000})

    r = create(env, network="net-p2")

    assert r.status_code == 400 and r.json() == {"detail": "caller identity cannot be sent to a peer service"}
    assert_nothing_left(env)
