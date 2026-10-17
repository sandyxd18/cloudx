from __future__ import annotations

import importlib
import os

import pytest
from fastapi.testclient import TestClient
from jose import jwt

AUTH = {"Authorization": "Bearer " + jwt.encode(
    {"project_id": "proj-1", "role": "member", "user_id": "usr-1"}, os.environ["AUTH_SECRET_KEY"], algorithm="HS256")}
IH = {"X-Internal-Token": "t" * 32}


@pytest.fixture
def ctx(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'k.sqlite'}")
    monkeypatch.setenv("INTERNAL_TOKEN", "t" * 32)
    import services.k8s_engine.app.main as m
    m = importlib.reload(m)
    m.Base.metadata.create_all(bind=m.engine)
    c = TestClient(m.app)
    tpl = c.post("/cluster-templates", json={"name": "t", "image_id": "img-1"}, headers=AUTH).json()["id"]
    cid = c.post("/clusters", json={"name": "c1", "template_id": tpl, "worker_count": 1, "network_id": "n1"}, headers=AUTH).json()["id"]
    return c, m, cid


def node(c, cid, role, sid):
    return c.post(f"/internal/k8s-engine/clusters/{cid}/nodes", json={"server_id": sid, "role": role}, headers=IH)


def test_internal_requires_token(ctx):
    c, _, cid = ctx
    for method, url in [("get", "/internal/k8s-engine/clusters"), ("post", f"/internal/k8s-engine/clusters/{cid}/ready"),
                        ("post", f"/internal/k8s-engine/clusters/{cid}/status"), ("delete", f"/internal/k8s-engine/clusters/{cid}")]:
        assert getattr(c, method)(url).status_code in (401, 403, 422), url
    assert c.post(f"/internal/k8s-engine/clusters/{cid}/ready", json={"role": "master", "ip": "10.0.0.5"}).status_code in (401, 403)


def test_user_api_never_leaks_tokens(ctx):
    c, _, cid = ctx
    for r in (c.get("/clusters", headers=AUTH).json()[0], c.get(f"/clusters/{cid}", headers=AUTH).json()):
        assert "join_token" not in r and "admin_token" not in r
    assert "join_token" in c.get("/internal/k8s-engine/clusters", headers=IH).json()[0]


def test_ready_callback_as_vm_sends_it(ctx):
    c, _, cid = ctx
    assert node(c, cid, "master", "srv-m").status_code == 201
    r = c.post(f"/internal/k8s-engine/clusters/{cid}/ready", json={"role": "master", "ip": "10.0.0.50", "node_name": "m"}, headers=IH)
    assert r.status_code == 200
    again = c.post(f"/internal/k8s-engine/clusters/{cid}/ready", json={"role": "master", "ip": "10.0.0.50"}, headers=IH)
    assert again.status_code == 200  # idempotent
    got = c.get("/internal/k8s-engine/clusters", headers=IH).json()[0]
    assert got["master_ip"] == "10.0.0.50" and [n["ready"] for n in got["nodes"]] == [True]


def test_ready_unknown_cluster_or_node_is_404(ctx):
    c, _, cid = ctx
    assert c.post("/internal/k8s-engine/clusters/cls-nope/ready", json={"role": "master", "ip": "10.0.0.5"}, headers=IH).status_code == 404
    assert c.post(f"/internal/k8s-engine/clusters/{cid}/ready", json={"role": "worker", "ip": "10.0.0.6"}, headers=IH).status_code == 404


def test_two_workers_become_ready_one_each(ctx):
    c, _, cid = ctx
    node(c, cid, "worker", "w1"); node(c, cid, "worker", "w2")
    for ip in ("10.0.0.6", "10.0.0.7"):
        assert c.post(f"/internal/k8s-engine/clusters/{cid}/ready", json={"role": "worker", "ip": ip}, headers=IH).status_code == 200
    nodes = c.get("/internal/k8s-engine/clusters", headers=IH).json()[0]["nodes"]
    assert sorted(n["ip"] for n in nodes) == ["10.0.0.6", "10.0.0.7"] and all(n["ready"] for n in nodes)


def test_delete_node(ctx):
    c, _, cid = ctx
    nid = node(c, cid, "worker", "w1").json()["id"]
    url = f"/internal/k8s-engine/clusters/{cid}/nodes/{nid}"
    assert c.delete(url).status_code in (401, 403)
    assert c.delete(f"/internal/k8s-engine/clusters/cls-other/nodes/{nid}", headers=IH).status_code == 404
    assert c.delete(url, headers=IH).status_code == 204
    assert c.get("/internal/k8s-engine/clusters", headers=IH).json()[0]["nodes"] == []
    assert c.delete(url, headers=IH).status_code == 404


def test_kubeconfig_needs_active_and_master_ip(ctx):
    c, _, cid = ctx
    assert c.get(f"/clusters/{cid}/kubeconfig", headers=AUTH).status_code == 409
    c.post(f"/internal/k8s-engine/clusters/{cid}/status", json={"status": "ACTIVE"}, headers=IH)
    assert c.get(f"/clusters/{cid}/kubeconfig", headers=AUTH).status_code == 409  # no master_ip yet
    c.post(f"/internal/k8s-engine/clusters/{cid}/status", json={"master_ip": "10.0.0.50"}, headers=IH)
    kc = c.get(f"/clusters/{cid}/kubeconfig", headers=AUTH).json()["kubeconfig"]
    tok = c.get("/internal/k8s-engine/clusters", headers=IH).json()[0]["admin_token"]
    assert "server: https://10.0.0.50:6443" in kc and f"token: {tok}" in kc


def test_kubeconfig_refused_until_active_even_with_master_ip(ctx):
    c, _, cid = ctx
    c.post(f"/internal/k8s-engine/clusters/{cid}/status", json={"master_ip": "10.0.0.50"}, headers=IH)
    assert c.get(f"/clusters/{cid}/kubeconfig", headers=AUTH).status_code == 409
    c.post(f"/internal/k8s-engine/clusters/{cid}/status", json={"status": "DELETING"}, headers=IH)
    assert c.get(f"/clusters/{cid}/kubeconfig", headers=AUTH).status_code == 409


def test_scale_and_delete_state_machine(ctx):
    c, _, cid = ctx
    assert c.post(f"/clusters/{cid}/scale", json={"worker_count": 3}, headers=AUTH).status_code == 409
    c.post(f"/internal/k8s-engine/clusters/{cid}/status", json={"status": "ACTIVE"}, headers=IH)
    r = c.post(f"/clusters/{cid}/scale", json={"worker_count": 3}, headers=AUTH)
    assert r.status_code == 200 and r.json()["status"] == "SCALING" and r.json()["worker_count"] == 3
    assert c.delete(f"/clusters/{cid}", headers=AUTH).status_code == 202
    assert c.get(f"/clusters/{cid}", headers=AUTH).json()["status"] == "DELETING"
    assert c.delete(f"/internal/k8s-engine/clusters/{cid}", headers=IH).status_code == 204
    assert c.get(f"/clusters/{cid}", headers=AUTH).status_code == 404

def test_delete_cluster_removes_nodes_first_with_foreign_keys_enforced(ctx):
    """SQLite ignores FKs by default; Postgres does not. Delete order must be nodes, then cluster."""
    from sqlalchemy import event
    c, m, cid = ctx
    event.listen(m.engine, "connect", lambda con, _: con.execute("PRAGMA foreign_keys=ON"))
    m.engine.dispose()
    node(c, cid, "master", "srv-m"); node(c, cid, "worker", "srv-w")
    assert c.delete(f"/internal/k8s-engine/clusters/{cid}", headers=IH).status_code == 204
    assert c.get("/internal/k8s-engine/clusters", headers=IH).json() == []
