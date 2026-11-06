"""The worker acts on compute and load-balancer as a member of the scaling group's project.

Part one captures the requests the worker sends and checks the headers. Part two wires the worker's `requests`
calls to the real compute app (with the real image-api and networking apps behind it) and the real load-balancer
app, so each service applies its own project rules to what the worker asks for.
"""
import importlib
import io
import json
import logging
from types import SimpleNamespace

import pytest
import requests
from fastapi.testclient import TestClient
from services.orchestration_worker.app import main as w
from shared.auth_middleware import require_auth, require_user_or_internal
from tests.stack import fresh_service

TOKEN = "s3cret"
COMPUTE_URL = "http://compute.test"
LB_URL = "http://lb.test"
IMAGE_URL = "http://image.test"
NET_URL = "http://networking.test"
GROUP = {"id": "g1", "name": "web", "project_id": "p2", "flavor_id": "small", "image_id": "img", "network_id": "net",
         "load_balancer_pool_id": "pool1"}
SERVER = {"id": "srv9", "ip_address": "10.0.0.9"}
IDENTITY = {"sub": "autoscale:g1", "username": "autoscale", "role": "member", "roles": ["member"],
            "project_id": "p2", "scope_type": "project"}


class Reply:
    def __init__(self, status_code, body=None):
        self.status_code, self._body = status_code, body
        self.text = json.dumps(body)

    def json(self):
        return self._body

    def raise_for_status(self):
        assert self.status_code < 400


@pytest.fixture(autouse=True)
def no_overrides(monkeypatch):
    monkeypatch.setenv("COMPUTE_API_URL", COMPUTE_URL)
    monkeypatch.setenv("LOAD_BALANCER_API_URL", LB_URL)
    monkeypatch.setenv("ORCHESTRATION_API_URL", "http://orchestration.test")
    monkeypatch.setenv("TELEMETRY_API_URL", "http://telemetry.test")


@pytest.fixture
def captured(monkeypatch):
    calls = []

    def record(method):
        def send(url, headers=None, json=None, timeout=None, **kw):
            calls.append((method, url, headers or {}))
            return SimpleNamespace(status_code=200, text="[]", json=lambda: [{"value": 1.0}])
        return send

    for method in ("get", "post", "delete"):
        monkeypatch.setattr(w.requests, method, record(method.upper()))
    return calls


def claims_of(headers):
    return json.loads(headers["X-Caller-Claims"])


# ── the headers each call sends ──────────────────────────────────────────────

GROUP_CALLS = {
    "spawn": lambda: w._spawn_server(GROUP, "web-asg-1"),
    "terminate": lambda: w._terminate_server("srv9", GROUP),
    "add_member": lambda: w._add_member_to_pool("pool1", SERVER, GROUP),
    "remove_member": lambda: w._remove_member_from_pool("pool1", "srv9", GROUP),
}


@pytest.mark.parametrize("name", GROUP_CALLS)
def test_group_actions_send_the_groups_identity(monkeypatch, captured, name):
    monkeypatch.setenv("INTERNAL_TOKEN", TOKEN)

    GROUP_CALLS[name]()

    (_, _, headers), = captured
    assert headers["X-Internal-Token"] == TOKEN
    assert headers["Content-Type"] == "application/json"
    assert claims_of(headers) == IDENTITY


def test_group_actions_state_the_project_of_the_group_they_are_given(monkeypatch, captured):
    monkeypatch.setenv("INTERNAL_TOKEN", TOKEN)

    w._spawn_server({**GROUP, "id": "g7", "project_id": "p7"}, "web-asg-1")

    assert claims_of(captured[0][2]) == {**IDENTITY, "sub": "autoscale:g7", "project_id": "p7"}


def test_reads_and_history_send_the_internal_token_only(monkeypatch, captured):
    monkeypatch.setenv("INTERNAL_TOKEN", TOKEN)

    w._get_scaling_groups()
    w._get_group_instances("g1")
    w._get_server("srv9")
    w._get_instance_avg_cpu(["srv9"])
    w._record_history("g1", "scale_out", "reason", "srv9", 80.0, 1, 2, "web-asg-1")

    assert [c[0] for c in captured] == ["GET", "GET", "GET", "GET", "POST"]
    for _, _, headers in captured:
        assert headers["X-Internal-Token"] == TOKEN
        assert "X-Caller-Claims" not in headers


@pytest.mark.parametrize("name", GROUP_CALLS)
def test_without_an_internal_token_no_claims_are_sent(monkeypatch, captured, name):
    monkeypatch.delenv("INTERNAL_TOKEN", raising=False)

    GROUP_CALLS[name]()

    (_, _, headers), = captured
    assert "X-Caller-Claims" not in headers and "X-Internal-Token" not in headers


FAILURE_VALUE = {"spawn": None, "terminate": False, "add_member": False, "remove_member": False}


@pytest.mark.parametrize("name", GROUP_CALLS)
@pytest.mark.parametrize("group", [{k: v for k, v in GROUP.items() if k != "project_id"}, {**GROUP, "project_id": ""},
                                   {**GROUP, "project_id": None}], ids=["missing", "empty", "none"])
def test_a_group_without_a_project_makes_no_call(monkeypatch, captured, caplog, name, group):
    monkeypatch.setenv("INTERNAL_TOKEN", TOKEN)
    calls = {
        "spawn": lambda: w._spawn_server(group, "web-asg-1"),
        "terminate": lambda: w._terminate_server("srv9", group),
        "add_member": lambda: w._add_member_to_pool("pool1", SERVER, group),
        "remove_member": lambda: w._remove_member_from_pool("pool1", "srv9", group),
    }

    with caplog.at_level(logging.ERROR, logger="orchestration-worker"):
        result = calls[name]()

    assert result == FAILURE_VALUE[name]
    assert captured == []
    assert "no project" in caplog.text


def test_oversized_identity_makes_no_call(monkeypatch, captured, caplog):
    monkeypatch.setenv("INTERNAL_TOKEN", TOKEN)

    with caplog.at_level(logging.ERROR, logger="orchestration-worker"):
        assert w._spawn_server({**GROUP, "project_id": "p" * 3000}, "web-asg-1") is None

    assert captured == []
    assert "2048 bytes" in caplog.text


def test_check_group_passes_the_group_to_every_action(monkeypatch):
    monkeypatch.setenv("INTERNAL_TOKEN", TOKEN)
    monkeypatch.setattr(w, "_peers_resolvable", lambda peers: True)
    group = {**GROUP, "name": "web", "target_cpu_percent": 50, "cooldown_seconds": 0, "scale_in_cooldown_seconds": 0,
             "min_instances": 1, "max_instances": 3}
    seen = []
    monkeypatch.setattr(w, "_record_history", lambda *a, **k: None)
    monkeypatch.setattr(w, "_get_server", lambda sid: SERVER)
    monkeypatch.setattr(w, "_spawn_server", lambda g, name: seen.append(("spawn", g["project_id"])) or {"id": "srv9"})
    monkeypatch.setattr(w, "_add_member_to_pool", lambda pool, server, g: seen.append(("add", g["project_id"])) or True)
    monkeypatch.setattr(w, "_remove_member_from_pool", lambda pool, sid, g: seen.append(("remove", g["project_id"])) or True)
    monkeypatch.setattr(w, "_terminate_server", lambda sid, g: seen.append(("terminate", g["project_id"])) or True)

    monkeypatch.setattr(w, "_get_group_instances", lambda gid: [{"status": "active", "server_id": "s1"}])
    monkeypatch.setattr(w, "_get_instance_avg_cpu", lambda ids: 90.0)
    w._check_group(group)
    monkeypatch.setattr(w, "_get_group_instances", lambda gid: [
        {"status": "active", "server_id": f"s{n}", "created_at": str(n)} for n in range(2)])
    monkeypatch.setattr(w, "_get_instance_avg_cpu", lambda ids: 1.0)
    w._check_group(group)

    assert seen == [("spawn", "p2"), ("add", "p2"), ("remove", "p2"), ("terminate", "p2")]


# ── the real services behind the worker ──────────────────────────────────────

def private_image(client, image_app, project, name):
    who = {"project_id": project, "role": "member", "roles": ["member"], "sub": f"u-{project}", "scope_type": "project"}
    for dep in (require_auth, require_user_or_internal):
        image_app.dependency_overrides[dep] = lambda: who  # only to create the image; cleared below
    r = client.post("/images", data={"name": name, "disk_format": "raw", "visibility": "private"},
                    files={"file": ("f.img", io.BytesIO(b"hello"))})
    image_app.dependency_overrides.clear()
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("INTERNAL_TOKEN", TOKEN)
    monkeypatch.setenv("REVOCATION_CHECK", "false")
    monkeypatch.setenv("IMAGE_API_URL", IMAGE_URL)
    monkeypatch.setenv("NETWORKING_API_URL", NET_URL)
    for key in ("BLOCK_STORE_API_URL", "PLACEMENT_API_URL"):
        monkeypatch.setenv(key, f"http://{key.split('_')[0].lower()}.test")
    monkeypatch.setattr("shared.domain.domain_projects", lambda d: [])

    # the real image-api, on its own database
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'i.sqlite'}")
    monkeypatch.setenv("IMAGE_STORE_PATH", str(tmp_path / "store"))
    import services.image.app.main as image

    image = importlib.reload(image)
    image.Base.metadata.create_all(bind=image.engine)
    image_client = TestClient(image.app)
    images = {"p1": private_image(image_client, image.app, "p1", "p1-image"),
              "p2": private_image(image_client, image.app, "p2", "p2-image")}

    # the real networking-api, ovn stubbed
    net = fresh_service("networking", monkeypatch, tmp_path)
    monkeypatch.setattr(net.ovn_cli, "ovn", lambda *a, **k: "")
    monkeypatch.setattr(net.events, "_rpc", type("Rpc", (), {"publish_event": lambda *a, **k: None})())
    m = net.models
    with net.db.SessionLocal() as db:
        db.add_all([m.Network(id="net-p1", name="p1-net", project_id="p1"),
                    m.Network(id="net-p2", name="p2-net", project_id="p2"),
                    m.SecurityGroup(id="sg-default", name="default", project_id="default")])
        for i, network in enumerate(["net-p1", "net-p2"]):
            db.add(m.Subnet(id=f"sub-{network}", name=f"sub-{network}", network_id=network, cidr=f"10.{i}.0.0/24",
                            gateway=f"10.{i}.0.1", ip_version=4))
        db.commit()
    net_client = TestClient(net.app, raise_server_exceptions=False)

    # the real compute-api: its own auth chain, so it reads the identity the worker states
    svc = fresh_service("compute", monkeypatch, tmp_path)
    compute_client = TestClient(svc.app, raise_server_exceptions=False)
    servers = importlib.import_module("services.compute.app.routers.servers")
    monkeypatch.setattr(servers, "_check_project_quota", lambda *a, **k: None)  # asks auth-api, not what is tested here
    monkeypatch.setattr(svc.clients, "cast_to_worker", lambda method, payload: None)
    monkeypatch.setattr(svc.clients, "block_post", lambda path, payload: {
        "id": "root1", "size_gb": 8, "status": "available", "path": "/volumes/root1.qcow2"})
    monkeypatch.setattr(svc.clients, "block_delete", lambda path: None)
    monkeypatch.setattr(svc.clients, "_rpc", type("Rpc", (), {"publish_event": lambda *a, **k: None})())
    with svc.SessionLocal() as db:
        db.add(svc.Flavor(name="small", vcpus=1, ram_mb=512, disk_gb=1))
        db.add_all([
            svc.Server(id="srv-p1", name="p1-vm", project_id="p1", image_id="img0", root_volume_id="vol1", flavor="small",
                       network_id="net-p1", port_id="port1", host="node-1", status="ACTIVE"),
            svc.Server(id="srv-p2", name="p2-vm", project_id="p2", image_id="img0", root_volume_id="vol2", flavor="small",
                       network_id="net-p2", port_id="port2", host="node-1", status="ACTIVE"),
        ])
        db.commit()

    # the real load-balancer-api, with a pool in each project
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'lb.sqlite'}")
    import services.load_balancer.app.main as lb

    lb = importlib.reload(lb)
    lb.Base.metadata.create_all(lb.engine)
    monkeypatch.setattr(lb, "_apply_lb_state", lambda db, load_balancer: None)
    with lb.SessionLocal() as db:
        for project in ("p1", "p2"):
            db.add(lb.LoadBalancer(id=f"lb-{project}", name=f"lb-{project}", project_id=project, lb_type="ALB",
                                   vip_address="10.0.0.5", provisioning_status="ACTIVE"))
            db.add(lb.Listener(id=f"ls-{project}", load_balancer_id=f"lb-{project}", name=f"ls-{project}",
                               protocol="HTTP", protocol_port=80))
            db.add(lb.Pool(id=f"pool-{project}", load_balancer_id=f"lb-{project}", listener_id=f"ls-{project}",
                           name=f"pool-{project}", protocol="HTTP", lb_algorithm="ROUND_ROBIN"))
        db.add(lb.Member(id="member-p1", pool_id="pool-p1", address="10.0.1.9", protocol_port=80, instance_id="srv-p1"))
        db.add(lb.Member(id="member-p2", pool_id="pool-p2", address="10.0.2.9", protocol_port=80, instance_id="srv-p2"))
        db.commit()
    lb_client = TestClient(lb.app, raise_server_exceptions=False)

    seen = {"claims": [], "released": [], "net_calls": []}
    targets = {COMPUTE_URL: compute_client, LB_URL: lb_client, IMAGE_URL: image_client, NET_URL: net_client}

    def dispatch(method):
        def send(url, headers=None, json=None, timeout=None, **kw):
            for base, client in targets.items():
                if url.startswith(base):
                    if base == NET_URL:
                        seen["net_calls"].append((method, url[len(base):]))
                    r = client.request(method, url[len(base):], headers=headers, json=json)
                    try:
                        body = r.json()
                    except ValueError:
                        body = None
                    return Reply(r.status_code, body)
            if "/internal/projects/" in url:  # networking's quota check asks auth-api
                return Reply(200, {})
            if url.endswith("/internal/claim"):  # placement claim
                seen["claims"].append(json["consumer_uuid"])
                return Reply(200, {"host": "node-1"})
            if "/internal/allocations/" in url:  # placement release
                seen["released"].append(url.rsplit("/", 1)[1])
                return Reply(200, {})
            raise AssertionError(f"unexpected {method} {url}")
        return send

    for method in ("get", "post", "delete"):
        monkeypatch.setattr(requests, method, dispatch(method.upper()))

    def server_rows():
        with svc.SessionLocal() as db:
            return {s.id: s for s in db.query(svc.Server)}

    def members(pool_id):
        with lb.SessionLocal() as db:
            return sorted(m.id for m in db.query(lb.Member).filter_by(pool_id=pool_id))

    def server_projects():
        return {sid: s.project_id for sid, s in server_rows().items()}

    return SimpleNamespace(images=images, seen=seen, server_projects=server_projects, members=members,
                           svc=svc, net=net)


def p2_group(env, **over):
    return {**GROUP, "image_id": env.images["p2"]["id"], "network_id": "net-p2", **over}


def test_spawn_lands_in_the_groups_project(env):
    server = w._spawn_server(p2_group(env), "web-asg-1")

    assert server is not None and server["status"] == "BUILD"
    assert env.server_projects()[server["id"]] == "p2"
    assert server["project_id"] == "p2"


def test_spawn_with_an_image_of_another_project_creates_nothing(env):
    before = env.server_projects()

    server = w._spawn_server(p2_group(env, image_id=env.images["p1"]["id"]), "web-asg-1")

    assert server is None
    assert env.server_projects() == before
    assert env.seen["released"] == env.seen["claims"]


def test_spawn_with_a_network_of_another_project_creates_nothing(env):
    before = env.server_projects()

    server = w._spawn_server(p2_group(env, network_id="net-p1"), "web-asg-1")

    assert server is None
    assert env.server_projects() == before


def test_terminate_removes_a_server_of_the_groups_project(env):
    assert w._terminate_server("srv-p2", p2_group(env)) is True

    with env.svc.SessionLocal() as db:
        assert db.get(env.svc.Server, "srv-p2").status == "DELETING"


def test_terminate_leaves_a_server_of_another_project_alone(env, caplog):
    with caplog.at_level(logging.WARNING, logger="orchestration-worker"):
        assert w._terminate_server("srv-p1", p2_group(env)) is True  # not found in the group's project: nothing to do

    assert "not found in project p2" in caplog.text

    with env.svc.SessionLocal() as db:
        assert db.get(env.svc.Server, "srv-p1").status == "ACTIVE"


def test_pool_of_the_groups_project_takes_and_drops_members(env):
    group = p2_group(env, load_balancer_pool_id="pool-p2")

    assert w._add_member_to_pool("pool-p2", {"id": "srv9", "ip_address": "10.0.2.10"}, group) is True
    assert len(env.members("pool-p2")) == 2

    assert w._remove_member_from_pool("pool-p2", "srv9", group) is True
    assert env.members("pool-p2") == ["member-p2"]


def test_pool_of_another_project_is_left_alone(env):
    group = p2_group(env, load_balancer_pool_id="pool-p1")

    assert w._add_member_to_pool("pool-p1", {"id": "srv9", "ip_address": "10.0.2.10"}, group) is False
    assert w._remove_member_from_pool("pool-p1", "srv-p1", group) is False

    assert env.members("pool-p1") == ["member-p1"]


def _scale_in_with(monkeypatch, terminate_result, recorded):
    monkeypatch.setenv("INTERNAL_TOKEN", TOKEN)
    monkeypatch.setattr(w, "_peers_resolvable", lambda peers: True)
    group = {**GROUP, "target_cpu_percent": 50, "cooldown_seconds": 0, "scale_in_cooldown_seconds": 0,
             "min_instances": 1, "max_instances": 3, "load_balancer_pool_id": None}
    monkeypatch.setattr(w, "_record_history", lambda *a, **k: recorded.append(a))
    monkeypatch.setattr(w, "_terminate_server", lambda sid, g: terminate_result)
    monkeypatch.setattr(w, "_get_group_instances", lambda gid: [
        {"status": "active", "server_id": f"s{n}", "created_at": str(n)} for n in range(2)])
    monkeypatch.setattr(w, "_get_instance_avg_cpu", lambda ids: 1.0)
    w._check_group(group)


def test_a_server_compute_refused_to_terminate_stays_in_the_group(monkeypatch):
    recorded = []
    _scale_in_with(monkeypatch, False, recorded)

    assert recorded == []  # no history, so the instance is kept and the next cycle tries again


def test_a_terminated_server_leaves_the_group(monkeypatch):
    recorded = []
    _scale_in_with(monkeypatch, True, recorded)

    assert [a[1] for a in recorded] == ["scale_in"]


def test_a_refused_pool_call_is_logged(env, caplog):
    group = {**p2_group(env), "load_balancer_pool_id": "pool-p1"}
    with caplog.at_level(logging.WARNING, logger="orchestration-worker"):
        assert w._add_member_to_pool("pool-p1", {"id": "srv9", "ip_address": "10.0.2.10"}, group) is False

    assert "refused to add server srv9 to pool pool-p1" in caplog.text
