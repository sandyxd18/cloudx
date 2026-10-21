from __future__ import annotations

import datetime
import logging
from types import SimpleNamespace

import pytest

from services.k8s_engine_worker.app import main as w

JT, AT, IT = "jointoken_0123456", "admintoken_0123456", "internal_0123456789"


def cluster(status="CREATING", nodes=None, workers=1, master_ip=None, age=10):
    created = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=age)).replace(tzinfo=None).isoformat()
    return {"id": "cls-1234567890ab", "name": "c1", "worker_count": workers, "network_id": "net-1", "status": status,
            "master_ip": master_ip, "created_at": created, "join_token": JT, "admin_token": AT, "nodes": nodes or []}


def n(role, sid, ready=False, ip=None, nid=None):
    return {"id": nid or f"node-{sid}", "server_id": sid, "role": role, "ready": ready, "ip": ip}


class Env:
    def __init__(self, mp):
        self.clusters, self.calls, self.fail_create, self.fail_delete, self.seq = [], [], False, set(), 0
        self.gone = set()
        mp.setenv("INTERNAL_TOKEN", IT)
        mp.setenv("K8S_ENGINE_API_URL", "http://k8s-engine.test:8015")
        mp.setenv("COMPUTE_API_URL", "http://compute.test:8003")
        w.CALLBACK_URL = "http://172.17.0.1:8015"
        mp.setattr(w.requests, "get", lambda url, **k: self._r(200, self.clusters))
        mp.setattr(w.requests, "post", self.post)
        mp.setattr(w.requests, "delete", self.delete)

    @staticmethod
    def _r(code, body=None):
        def raise_for_status():
            if code >= 400:
                raise RuntimeError(f"HTTP {code}")
        return SimpleNamespace(status_code=code, json=lambda: body, raise_for_status=raise_for_status)

    def post(self, url, json=None, **k):
        self.calls.append(("POST", url, json))
        if url.endswith("/servers"):
            if self.fail_create:
                return self._r(500)
            self.seq += 1
            return self._r(201, {"id": f"srv-{self.seq}"})
        return self._r(201 if url.endswith("/nodes") else 200, {})

    def delete(self, url, **k):
        self.calls.append(("DELETE", url, None))
        sid = url.rsplit("/", 1)[-1]
        return self._r(500 if sid in self.fail_delete else 404 if sid in self.gone else 204)

    def of(self, method, suffix):
        return [c for c in self.calls if c[0] == method and c[1].endswith(suffix)]

    def servers(self):
        return self.of("POST", "/servers")


@pytest.fixture
def env(monkeypatch):
    return Env(monkeypatch)


def test_master_first_with_exact_payload_then_node_recorded(env):
    env.clusters = [cluster()]
    w.tick()
    (_, _, body), = env.servers()
    assert set(body) == {"name", "image", "flavor", "network", "user_data"}
    assert body["name"] == "c1-7890ab-master"
    assert body["network"] == "net-1" and body["flavor"] == w.MASTER_FLAVOR and body["image"] == w.VM_IMAGE
    assert "k3s server" in body["user_data"]
    (_, _, rec), = env.of("POST", "/nodes")
    assert rec == {"server_id": "srv-1", "role": "master"}
    assert len(env.servers()) == 1  # no worker before the master is ready


def test_no_duplicate_master_when_already_recorded(env):
    env.clusters = [cluster(nodes=[n("master", "srv-1")])]
    w.tick(); w.tick()
    assert env.servers() == []


def test_workers_only_after_master_ready_and_only_missing_ones(env):
    env.clusters = [cluster(nodes=[n("master", "srv-1", ready=False)], workers=2)]
    w.tick()
    assert env.calls == []  # no VM and no ERROR status while the master boots
    env.clusters = [cluster(nodes=[n("master", "srv-1", True, "10.0.0.5"), n("worker", "srv-2")], workers=2, master_ip="10.0.0.5")]
    w.tick()
    (_, _, body), = env.servers()
    assert "k3s agent --server https://10.0.0.5:6443" in body["user_data"] and body["name"].endswith("-worker-2")


def test_active_only_when_everything_ready(env):
    ms = n("master", "srv-1", True, "10.0.0.5")
    env.clusters = [cluster(nodes=[ms, n("worker", "srv-2", False)], master_ip="10.0.0.5")]
    w.tick()
    assert env.of("POST", "/status") == []
    env.clusters = [cluster(nodes=[ms, n("worker", "srv-2", True, "10.0.0.6")], master_ip="10.0.0.5")]
    w.tick()
    assert env.of("POST", "/status")[0][2]["status"] == "ACTIVE"


def test_timeout_becomes_error(env):
    env.clusters = [cluster(age=w.READY_TIMEOUT + 5)]
    w.tick()
    assert env.servers() == []
    assert env.of("POST", "/status")[0][2] == {"status": "ERROR", "status_reason": "timeout waiting for nodes"}


def test_create_failure_sets_error_and_removes_existing_vms(env):
    env.clusters = [cluster(nodes=[n("master", "srv-m", True, "10.0.0.5")], master_ip="10.0.0.5", workers=1)]
    env.fail_create = True
    w.tick()
    assert env.of("POST", "/status")[0][2]["status"] == "ERROR"
    assert env.of("DELETE", "/servers/srv-m")


def test_unrecordable_vm_is_removed(env, monkeypatch):
    env.clusters = [cluster()]
    real = env.post
    monkeypatch.setattr(w.requests, "post", lambda url, json=None, **k: env._r(500) if url.endswith("/nodes") else real(url, json=json, **k))
    w.tick()
    assert env.of("DELETE", "/servers/srv-1")


def test_scale_up_down_and_active(env):
    ms = n("master", "srv-m", True, "10.0.0.5")
    ws = [n("worker", "srv-w1", True, "10.0.0.6", "node-w1"), n("worker", "srv-w2", True, "10.0.0.7", "node-w2")]
    env.clusters = [cluster("SCALING", [ms] + ws, workers=3, master_ip="10.0.0.5")]
    w.tick()
    assert len(env.servers()) == 1 and env.servers()[0][2]["name"].endswith("-worker-3")
    env.calls.clear()
    env.clusters = [cluster("SCALING", [ms] + ws, workers=1, master_ip="10.0.0.5")]
    w.tick()
    assert env.of("DELETE", "/servers/srv-w2") and not env.of("DELETE", "/servers/srv-w1")
    assert env.of("DELETE", "/nodes/node-w2")
    env.calls.clear()
    env.clusters = [cluster("SCALING", [ms, ws[0]], workers=1, master_ip="10.0.0.5")]
    w.tick()
    assert env.of("POST", "/status")[0][2]["status"] == "ACTIVE"


def test_scale_down_to_zero_then_up_again(env):
    ms = n("master", "srv-m", True, "10.0.0.5")
    env.clusters = [cluster("SCALING", [ms, n("worker", "srv-w1", True, "10.0.0.6", "node-w1")], workers=0, master_ip="10.0.0.5")]
    w.tick()
    assert env.of("DELETE", "/servers/srv-w1")
    env.calls.clear()
    env.clusters = [cluster("SCALING", [ms], workers=2, master_ip="10.0.0.5")]
    w.tick()
    assert [c[2]["name"][-8:] for c in env.servers()] == ["worker-1", "worker-2"]


def test_delete_removes_every_vm_before_the_row(env):
    env.clusters = [cluster("DELETING", [n("master", "srv-m"), n("worker", "srv-w")])]
    w.tick()
    kinds = [c[1].rsplit("/", 1)[-1] for c in env.calls if c[0] == "DELETE"]
    assert kinds == ["srv-m", "srv-w", "cls-1234567890ab"]


def test_delete_during_creating_leaves_no_vm_and_failure_keeps_the_row(env):
    env.clusters = [cluster("DELETING", [n("master", "srv-m")])]
    env.fail_delete = {"srv-m"}
    w.tick()
    assert not env.of("DELETE", "/clusters/cls-1234567890ab")


def test_idle_cluster_makes_no_compute_calls(env):
    env.clusters = [cluster("ACTIVE", [n("master", "srv-m", True, "10.0.0.5")], workers=0, master_ip="10.0.0.5")]
    w.tick(); w.tick()
    assert env.calls == []


def test_tokens_never_logged(env, caplog):
    caplog.set_level(logging.DEBUG)
    env.fail_create = True
    env.clusters = [cluster()]
    w.tick()
    assert JT not in caplog.text and AT not in caplog.text and IT not in caplog.text


def test_vm_already_gone_is_not_an_error(env):
    env.gone = {"srv-m"}
    env.clusters = [cluster("DELETING", [n("master", "srv-m")])]
    w.tick()
    assert env.of("DELETE", "/clusters/cls-1234567890ab")
