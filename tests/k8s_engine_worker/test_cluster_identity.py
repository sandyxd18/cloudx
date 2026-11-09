"""The worker creates and deletes cluster VMs as a member of the cluster's project.

Part one captures the requests the worker sends and checks the headers. Part two wires the worker's `requests`
calls to the real compute app (with the real image-api and networking apps behind it), so each service applies
its own project rules to what the worker asks for.
"""
import importlib
import io
import json
from types import SimpleNamespace

import pytest
import requests
from fastapi.testclient import TestClient
from services.k8s_engine_worker.app import main as w
from shared.auth_middleware import require_auth, require_user_or_internal
from tests.networking_peer import Reply, networking_peer
from tests.stack import fresh_service

TOKEN = "s3cret"
COMPUTE_URL = "http://compute.test"
IMAGE_URL = "http://image.test"
CLUSTER = {"id": "cls-1234567890ab", "name": "c1", "project_id": "p2", "network_id": "net-p2",
           "created_at": "2026-01-01T00:00:00", "join_token": "j", "admin_token": "a"}
IDENTITY = {"sub": "k8s-cluster:cls-1234567890ab", "username": "k8s-engine", "role": "member", "roles": ["member"],
            "project_id": "p2", "scope_type": "project"}


@pytest.fixture(autouse=True)
def urls(monkeypatch):
    monkeypatch.setenv("COMPUTE_API_URL", COMPUTE_URL)
    monkeypatch.setenv("K8S_ENGINE_API_URL", "http://k8s-engine.test")


@pytest.fixture
def captured(monkeypatch):
    calls = []

    def record(method):
        def send(url, headers=None, json=None, timeout=None, **kw):
            calls.append((method, url, headers or {}))
            return SimpleNamespace(status_code=200, text="{}", json=lambda: {"id": "srv1"}, raise_for_status=lambda: None)
        return send

    for method in ("get", "post", "delete"):
        monkeypatch.setattr(w.requests, method, record(method.upper()))
    return calls


def claims_of(headers):
    return json.loads(headers["X-Caller-Claims"])


VM_CALLS = {
    "create": lambda cluster: w._create_vm(cluster, "c1-master", "small", "ud"),
    "delete": lambda cluster: w._delete_vm(cluster, "srv9"),
}


@pytest.mark.parametrize("name", VM_CALLS)
def test_vm_calls_send_the_clusters_identity(monkeypatch, captured, name):
    monkeypatch.setenv("INTERNAL_TOKEN", TOKEN)

    VM_CALLS[name](CLUSTER)

    (_, _, headers), = captured
    assert headers["X-Internal-Token"] == TOKEN
    assert claims_of(headers) == IDENTITY


def test_vm_calls_state_the_project_of_the_cluster_they_are_given(monkeypatch, captured):
    monkeypatch.setenv("INTERNAL_TOKEN", TOKEN)

    w._create_vm({**CLUSTER, "id": "cls-other", "project_id": "p7"}, "c1-master", "small", "ud")

    assert claims_of(captured[0][2]) == {**IDENTITY, "sub": "k8s-cluster:cls-other", "project_id": "p7"}


def test_record_status_and_read_calls_send_the_internal_token_only(monkeypatch, captured):
    monkeypatch.setenv("INTERNAL_TOKEN", TOKEN)

    w._record_node("cls-1", "srv9", "master")
    w._delete_node_record("cls-1", "node-1")
    w._set_status("cls-1", "ACTIVE")
    w.tick()

    assert len(captured) == 4
    for _, _, headers in captured:
        assert headers["X-Internal-Token"] == TOKEN
        assert "X-Caller-Claims" not in headers


@pytest.mark.parametrize("name", VM_CALLS)
def test_without_an_internal_token_no_claims_are_sent(monkeypatch, captured, name):
    monkeypatch.delenv("INTERNAL_TOKEN", raising=False)

    VM_CALLS[name](CLUSTER)

    (_, _, headers), = captured
    assert "X-Caller-Claims" not in headers and "X-Internal-Token" not in headers


@pytest.mark.parametrize("name", VM_CALLS)
@pytest.mark.parametrize("cluster", [{k: v for k, v in CLUSTER.items() if k != "project_id"}, {**CLUSTER, "project_id": ""},
                                     {**CLUSTER, "project_id": None}], ids=["missing", "empty", "none"])
def test_a_cluster_without_a_project_makes_no_call(monkeypatch, captured, name, cluster):
    monkeypatch.setenv("INTERNAL_TOKEN", TOKEN)

    with pytest.raises(ValueError, match="no project"):
        VM_CALLS[name](cluster)

    assert captured == []


def test_spawn_and_cleanup_pass_the_cluster(monkeypatch):
    seen = []
    monkeypatch.setattr(w, "_create_vm", lambda cluster, *a: seen.append(("create", cluster["project_id"])) or "srv1")
    monkeypatch.setattr(w, "_delete_vm", lambda cluster, sid: seen.append(("delete", cluster["project_id"])))
    monkeypatch.setattr(w, "_record_node", lambda *a: (_ for _ in ()).throw(RuntimeError("no record")))

    with pytest.raises(RuntimeError):
        w._spawn(CLUSTER, "master", "c1-master", "small", "ud")

    assert seen == [("create", "p2"), ("delete", "p2")]


# ── the real services behind the worker ──────────────────────────────────────

def image_of(client, image_app, project, name):
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
    monkeypatch.setenv("IMAGE_API_URL", IMAGE_URL)
    for key in ("BLOCK_STORE_API_URL", "PLACEMENT_API_URL"):
        monkeypatch.setenv(key, f"http://{key.split('_')[0].lower()}.test")
    monkeypatch.setattr("shared.domain.domain_projects", lambda d: [])

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'i.sqlite'}")
    monkeypatch.setenv("IMAGE_STORE_PATH", str(tmp_path / "store"))
    import services.image.app.main as image

    image = importlib.reload(image)
    image.Base.metadata.create_all(bind=image.engine)
    image_client = TestClient(image.app)
    p2_image = image_of(image_client, image.app, "p2", "p2-image")

    net, _ = networking_peer(monkeypatch, tmp_path)  # real networking-api: net-p1 / net-p2, one private network each
    peer_request, peer_get = requests.request, requests.get

    svc = fresh_service("compute", monkeypatch, tmp_path)
    compute_client = TestClient(svc.app, raise_server_exceptions=False)
    servers = importlib.import_module("services.compute.app.routers.servers")
    monkeypatch.setattr(servers, "_check_project_quota", lambda *a, **k: None)
    monkeypatch.setattr(svc.clients, "cast_to_worker", lambda method, payload: None)
    monkeypatch.setattr(svc.clients, "block_post", lambda path, payload: {
        "id": "root1", "size_gb": 8, "status": "available", "path": "/volumes/root1.qcow2"})
    monkeypatch.setattr(svc.clients, "block_delete", lambda path: None)
    monkeypatch.setattr(svc.clients, "_rpc", type("Rpc", (), {"publish_event": lambda *a, **k: None})())
    with svc.SessionLocal() as db:
        db.add(svc.Flavor(name="small", vcpus=1, ram_mb=512, disk_gb=1))
        for project in ("p1", "p2"):
            db.add(svc.Server(id=f"srv-{project}", name=f"{project}-vm", project_id=project, image_id="img0",
                              root_volume_id=f"vol-{project}", flavor="small", network_id=f"net-{project}",
                              port_id=f"port-{project}", host="node-1", status="ACTIVE"))
        db.commit()

    def dispatch(method):
        def send(url, headers=None, json=None, timeout=None, **kw):
            for base, client in ((COMPUTE_URL, compute_client), (IMAGE_URL, image_client)):
                if url.startswith(base):
                    r = client.request(method, url[len(base):], headers=headers, json=json)
                    try:
                        body = r.json()
                    except ValueError:
                        body = None
                    return Reply(r.status_code, body)
            if url.endswith("/internal/claim"):
                return Reply(200, {"host": "node-1"})
            if "/internal/allocations/" in url:
                return Reply(200, {})
            return (peer_get(url, headers=headers) if method == "GET" else peer_request(method, url, headers=headers, json=json))
        return send

    for method in ("get", "post", "delete"):
        monkeypatch.setattr(requests, method, dispatch(method.upper()))

    def projects():
        with svc.SessionLocal() as db:
            return {s.id: s.project_id for s in db.query(svc.Server)}

    def status(server_id):
        with svc.SessionLocal() as db:
            return db.get(svc.Server, server_id).status

    monkeypatch.setattr(w, "VM_IMAGE", p2_image["id"])
    return SimpleNamespace(projects=projects, status=status)


def test_vm_of_the_clusters_own_network_belongs_to_its_project(env):
    server_id = w._create_vm(CLUSTER, "c1-master", "small", "ud")

    assert env.projects()[server_id] == "p2"


def test_vm_on_a_private_network_of_another_project_is_not_created(env):
    before = env.projects()

    with pytest.raises(requests.HTTPError) as err:
        w._create_vm({**CLUSTER, "network_id": "net-p1"}, "c1-master", "small", "ud")

    assert err.value.response.status_code == 404
    assert env.projects() == before


def test_delete_removes_a_server_of_the_clusters_project(env):
    w._delete_vm(CLUSTER, "srv-p2")

    assert env.status("srv-p2") == "DELETING"


def test_delete_leaves_a_server_of_another_project_alone(env):
    w._delete_vm(CLUSTER, "srv-p1")  # not found in the cluster's project: nothing to delete

    assert env.status("srv-p1") == "ACTIVE"
