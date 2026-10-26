from pathlib import Path

import pytest
from tests.stack import fresh_service


@pytest.fixture
def compute(monkeypatch, tmp_path: Path):
    svc = fresh_service("compute", monkeypatch, tmp_path)
    db = svc.SessionLocal()
    yield svc, db
    db.close()


def add_server(svc, db, name, host, status="ACTIVE"):
    db.add(svc.Server(id=f"id-{name}", name=name, image_id="img", network_id="net", port_id="port", flavor="tiny", status=status, host=host, backend="libvirt"))
    db.commit()


def test_new_server_goes_to_the_node_with_fewest_servers(compute):
    svc, db = compute
    add_server(svc, db, "a1", "node-a")
    add_server(svc, db, "a2", "node-a")
    add_server(svc, db, "b1", "node-b")
    assert svc.schedule_host(db, ["node-a", "node-b"]) == "node-b"


def test_deleted_servers_do_not_count(compute):
    svc, db = compute
    add_server(svc, db, "gone", "node-b", status="DELETED")
    add_server(svc, db, "a1", "node-a")
    assert svc.schedule_host(db, ["node-a", "node-b"]) == "node-b"


def test_ties_keep_the_first_listed_node(compute):
    svc, db = compute
    assert svc.schedule_host(db, ["node-a", "node-b"]) == "node-a"


def test_no_known_nodes_means_no_choice(compute):
    svc, db = compute
    assert svc.schedule_host(db, []) is None


def test_configured_hosts_win_over_placement(monkeypatch, compute):
    svc, _ = compute
    monkeypatch.setattr(svc.service, "COMPUTE_HOSTS", ["x", "y"])
    monkeypatch.setattr(svc.service.requests, "get", lambda *a, **k: pytest.fail("placement must not be asked"))
    assert svc.compute_hosts() == ["x", "y"]


def test_hosts_come_from_placement_providers_otherwise(monkeypatch, compute):
    svc, _ = compute
    monkeypatch.setenv("PLACEMENT_API_URL", "http://placement.test:8006")
    monkeypatch.setattr(svc.service, "COMPUTE_HOSTS", [])

    class Reply:
        def raise_for_status(self):
            pass

        def json(self):
            return {"resource_providers": [{"name": "cloudx-compute-01"}, {"name": "cloudx-storage-01"}]}

    asked = []
    monkeypatch.setattr(svc.service.requests, "get", lambda url, *a, **k: asked.append(url) or Reply())
    assert svc.compute_hosts() == ["cloudx-compute-01", "cloudx-storage-01"]
    # /resource_providers wants a user token and answers 401 to the internal token compute-api sends
    assert asked == ["http://placement.test:8006/internal/resource_providers"]


def test_placement_being_down_falls_back_to_any_worker(monkeypatch, compute):
    svc, _ = compute
    monkeypatch.setenv("PLACEMENT_API_URL", "http://placement.test:8006")
    monkeypatch.setattr(svc.service, "COMPUTE_HOSTS", [])

    def down(*a, **k):
        raise svc.service.requests.ConnectionError("placement is down")

    monkeypatch.setattr(svc.service.requests, "get", down)
    assert svc.compute_hosts() == []


def test_messages_are_addressed_to_the_servers_node(compute):
    svc, _ = compute
    assert svc.clients.worker_queue({"host": "cloudx-storage-01"}) == "compute.cloudx-storage-01"


def test_servers_without_a_usable_host_use_the_shared_queue(compute):
    svc, _ = compute
    assert svc.clients.worker_queue({"host": None}) == "compute"
    assert svc.clients.worker_queue({}) == "compute"
    assert svc.clients.worker_queue({"host": "single-node"}) == "compute"  # recorded by workers before multi-node


def test_cast_and_call_use_the_nodes_queue(monkeypatch, compute):
    svc, _ = compute
    seen = []

    class Rpc:
        def cast(self, key, method, payload):
            seen.append(("cast", key))

        def call(self, key, method, payload, timeout=None):
            seen.append(("call", key))
            return {}

    monkeypatch.setattr(svc.clients, "_rpc", Rpc())
    svc.clients.cast_to_worker("start", {"id": "s1", "host": "node-a"})
    svc.clients.call_worker("get_logs", {"id": "s1", "host": "node-b"})
    assert seen == [("cast", "compute.node-a"), ("call", "compute.node-b")]
