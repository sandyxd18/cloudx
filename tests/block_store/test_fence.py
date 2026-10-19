import pytest
from tests.stack import fresh_service

ADMIN = {"project_id": "p", "role": "admin"}


@pytest.fixture
def block(monkeypatch, tmp_path):
    monkeypatch.setenv("BLOCK_STORE_LVM_HOSTS", "node-a,node-b")
    monkeypatch.setenv("STORAGE_LVM_DRIVER", "true")
    svc = fresh_service("block_store", monkeypatch, tmp_path)
    calls, bad = [], {}

    class FakeRpc:
        def cast(self, *a):
            pass

        def call(self, key, method, args, timeout=None):
            calls.append((key, method, args))
            return {"error": "boom"} if args["volume_id"] == bad.get("id") else {}

    monkeypatch.setattr(svc.clients, "_rpc", FakeRpc())
    db = svc.SessionLocal()
    db.add(svc.VolumeType(id="vt", name="default", backend="lvm", is_public=True))
    db.commit()
    good = svc.create_volume(svc.VolumeIn(name="g", size_gb=1), ADMIN, db)
    bad.update(svc.create_volume(svc.VolumeIn(name="b", size_gb=1), ADMIN, db))
    yield svc, db, calls, good, bad
    db.close()


def test_fence_reports_per_volume(block):
    svc, db, calls, good, bad = block
    out = svc.fence({"initiator_iqn": "iqn.x", "volume_ids": [good["id"], bad["id"]]}, db)
    assert out["results"][good["id"]] == "ok"
    assert out["results"][bad["id"]].startswith("error: boom")
    assert calls[0][1] == "unexport_volume" and calls[0][2]["initiator_iqn"] == "iqn.x"
    assert calls[0][0] == f"block-store.{good['host']}"
