from fastapi.testclient import TestClient
from shared.auth_middleware import require_auth

from tests.stack import fresh_service


def _setup(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'rep_api.sqlite'}")
    monkeypatch.setenv("STORAGE_LVM_DRIVER", "true")
    bs = fresh_service("block_store", monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(bs.clients, "cast_to_worker", lambda m, p: calls.append((m, p)))
    bs.app.dependency_overrides[require_auth] = lambda: {"project_id": "proj-1", "role": "admin"}
    return bs, TestClient(bs.app), calls


def _vol(bs, vid, **kw):
    from services.block_store.app.models import Volume
    with bs.SessionLocal() as db:
        db.add(Volume(id=vid, name=vid, project_id="proj-1", size_gb=1, status="available", backend="lvm",
                      host="node-1", **kw))
        db.commit()


def _set(bs, vid, **kw):
    from services.block_store.app.models import Volume
    with bs.SessionLocal() as db:
        v = db.get(Volume, vid)
        for k, val in kw.items():
            setattr(v, k, val)
        db.commit()


def test_replication_workflow_and_guards(monkeypatch, tmp_path):
    bs, c, calls = _setup(monkeypatch, tmp_path)
    _vol(bs, "v1", replication_status="disabled")
    _vol(bs, "v3", replication_status="disabled")

    assert c.post("/volumes/v1/enable-replication", json={"target": "node-1", "mode": "async"}).status_code == 400
    assert c.post("/volumes/v3/sync-replication").status_code == 400

    r = c.post("/volumes/v1/enable-replication", json={"target": "node-2", "mode": "async"})
    assert r.status_code == 200 and calls[-1][0] == "replica_create" and calls[-1][1]["host"] == "node-2"

    assert c.post("/volumes/v1/sync-replication").json()["status"] == "replicating"
    assert calls[-1][0] == "replica_sync" and calls[-1][1]["host"] == "node-1"

    assert c.post("/volumes/v1/failover").status_code == 409  # not synced yet
    _set(bs, "v1", replication_status="synced", status="in-use")
    n = len(calls)
    assert c.post("/volumes/v1/failover").status_code == 409  # in-use without force
    assert len(calls) == n
    r = c.post("/volumes/v1/failover?force=true")
    assert r.status_code == 200 and r.json()["replication_status"] == "failed-over"
    assert calls[-1][0] == "replica_promote" and calls[-1][1]["host"] == "node-2"

    assert c.post("/volumes/v1/disable-replication").json()["replication_status"] == "disabled"
    assert calls[-1][0] == "replica_delete"
