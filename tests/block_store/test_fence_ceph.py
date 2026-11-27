"""POST /internal/fence for Ceph volumes: they have no host, so any block-store worker cuts off the client."""
import pytest
from tests.stack import fresh_service

ADMIN = {"project_id": "p", "role": "admin"}


@pytest.fixture
def block(monkeypatch, tmp_path):
    monkeypatch.setenv("BLOCK_STORE_LVM_HOSTS", "node-a,node-b")
    monkeypatch.setenv("STORAGE_LVM_DRIVER", "true")
    svc = fresh_service("block_store", monkeypatch, tmp_path)
    calls, reply = [], {}

    class FakeRpc:
        def cast(self, *a):
            pass

        def call(self, key, method, args, timeout=None):
            calls.append((key, method, args))
            return dict(reply)

    monkeypatch.setattr(svc.clients, "_rpc", FakeRpc())
    db = svc.SessionLocal()
    db.add(svc.VolumeType(id="vt", name="default", backend="lvm", is_public=True))
    db.commit()
    lvm = svc.create_volume(svc.VolumeIn(name="l", size_gb=1), ADMIN, db)
    ceph = svc.create_volume(svc.VolumeIn(name="c", size_gb=1), ADMIN, db)
    row = db.get(svc.Volume, ceph["id"])
    row.backend, row.host = "ceph", None
    db.commit()
    yield svc, db, calls, reply, lvm, ceph
    db.close()


def test_a_ceph_volume_is_fenced_by_any_worker_and_needs_no_iqn(block):
    svc, db, calls, reply, lvm, ceph = block
    reply["result"] = "ok"
    out = svc.fence({"volume_ids": [ceph["id"]]}, db)
    assert out["results"] == {ceph["id"]: "ok"}
    assert calls == [("block-store", "fence_volume", {"volume_id": ceph["id"]})]  # the shared queue: no host


def test_a_ceph_volume_with_no_client_reports_no_watcher(block):
    svc, db, calls, reply, lvm, ceph = block
    reply["result"] = "no-watcher"
    assert svc.fence({"volume_ids": [ceph["id"]]}, db)["results"] == {ceph["id"]: "no-watcher"}


def test_a_failing_ceph_fence_is_reported_as_an_error(block, monkeypatch):
    svc, db, calls, reply, lvm, ceph = block
    reply["error"] = "rbd: boom"
    assert svc.fence({"volume_ids": [ceph["id"]]}, db)["results"][ceph["id"]].startswith("error: rbd: boom")


def test_a_mixed_request_fences_each_volume_its_own_way(block):
    svc, db, calls, reply, lvm, ceph = block
    reply["result"] = "ok"
    out = svc.fence({"initiator_iqn": "iqn.x", "volume_ids": [lvm["id"], ceph["id"]]}, db)
    assert out["results"] == {lvm["id"]: "ok", ceph["id"]: "ok"}
    assert [c[1] for c in calls] == ["unexport_volume", "fence_volume"]
    assert calls[0][0] == f"block-store.{lvm['host']}"


def test_an_lvm_volume_without_an_iqn_is_an_error_and_sends_nothing(block):
    svc, db, calls, reply, lvm, ceph = block
    out = svc.fence({"initiator_iqn": "", "volume_ids": [lvm["id"]]}, db)
    assert out["results"][lvm["id"]].startswith("error:") and "iqn" in out["results"][lvm["id"]]
    assert calls == []
