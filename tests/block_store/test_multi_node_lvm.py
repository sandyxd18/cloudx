import pytest
from tests.stack import fresh_service

USER = {"project_id": "proj-1", "role": "admin"}


@pytest.fixture
def block(monkeypatch, tmp_path):
    monkeypatch.setenv("BLOCK_STORE_LVM_HOSTS", "node-a,node-b")
    monkeypatch.setenv("STORAGE_LVM_DRIVER", "true")
    svc = fresh_service("block_store", monkeypatch, tmp_path)
    casts = []

    class FakeRpc:
        def cast(self, routing_key, method, payload):
            casts.append((routing_key, method, payload))

        def publish_event(self, *a, **k):
            pass

    monkeypatch.setattr(svc.clients, "_rpc", FakeRpc())
    db = svc.SessionLocal()
    db.add(svc.VolumeType(id="vt", name="default", backend="lvm", is_public=True))
    db.commit()
    yield svc, db, casts
    db.close()


def test_new_volumes_go_to_the_node_with_the_least_allocated_space(block):
    svc, db, casts = block
    first = svc.create_volume(svc.VolumeIn(name="v1", size_gb=10), USER, db)
    second = svc.create_volume(svc.VolumeIn(name="v2", size_gb=1), USER, db)
    third = svc.create_volume(svc.VolumeIn(name="v3", size_gb=1), USER, db)
    assert [first["host"], second["host"], third["host"]] == ["node-a", "node-b", "node-b"]
    assert [c[0] for c in casts] == ["block-store.node-a", "block-store.node-b", "block-store.node-b"]
    assert all(c[1] == "create_volume" for c in casts)


def test_deleting_volumes_do_not_count_as_used_space(block):
    svc, db, _ = block
    big = svc.create_volume(svc.VolumeIn(name="big", size_gb=50), USER, db)
    db.get(svc.Volume, big["id"]).status = "deleting"
    db.commit()
    assert svc.pick_lvm_host(db) == "node-a"


def test_no_configured_hosts_keeps_the_shared_queue(monkeypatch, tmp_path):
    monkeypatch.delenv("BLOCK_STORE_LVM_HOSTS", raising=False)
    svc = fresh_service("block_store", monkeypatch, tmp_path)
    db = svc.SessionLocal()
    assert svc.pick_lvm_host(db) is None
    assert svc.pick_lvm_host(db, ["x"]) == "x"
    db.close()


def test_operations_on_a_volume_reach_the_node_that_owns_it(block):
    svc, db, casts = block
    vol = svc.create_volume(svc.VolumeIn(name="v1", size_gb=5), USER, db)
    svc.create_volume(svc.VolumeIn(name="v2", size_gb=1), USER, db)  # lands on the other node
    casts.clear()

    svc.extend_volume("v1", {"new_size": 8}, USER, db)
    svc.delete_volume("v1", USER, db)
    assert [(c[0], c[1]) for c in casts] == [("block-store.node-a", "extend_volume"), ("block-store.node-a", "delete_volume")]
    assert vol["host"] == "node-a"


def test_snapshots_and_clones_stay_on_the_source_node(block):
    svc, db, casts = block
    svc.create_volume(svc.VolumeIn(name="v1", size_gb=5), USER, db)  # node-a
    svc.create_volume(svc.VolumeIn(name="filler", size_gb=1), USER, db)  # node-b, so node-b now has less space than node-a
    v = db.get(svc.Volume, svc.require_volume(db, "v1").id)
    v.status, v.path = "available", "/dev/vg/v1"
    db.commit()
    casts.clear()

    snap = svc.create_volume_snapshot("v1", svc.SnapshotIn(name="s1"), USER, db)
    s = db.get(svc.Snapshot, snap["id"])
    s.status, s.path = "available", "/dev/vg/s1"  # the worker does this
    db.commit()
    clone = svc.create_volume(svc.VolumeIn(name="clone", size_gb=5, source_volume="v1"), USER, db)
    from_snap = svc.create_volume(svc.VolumeIn(name="restored", size_gb=5, snapshot="s1"), USER, db)
    svc.delete_snapshot("s1", USER, db)

    assert clone["host"] == from_snap["host"] == "node-a"
    assert [c[0] for c in casts] == ["block-store.node-a"] * 4  # snapshot, clone, restore, snapshot delete
    assert snap["volume_id"] == svc.require_volume(db, "v1").id


def test_volumes_without_a_host_use_the_shared_queue(block):
    svc, db, casts = block
    db.add(svc.Volume(id="legacy", name="legacy", size_gb=1, status="available", backend="nfs", path="/x.qcow2"))
    db.commit()
    svc.delete_volume("legacy", USER, db)
    assert casts[-1][0] == "block-store"


def test_a_consistency_group_cannot_span_nodes(block):
    svc, db, _ = block
    a = svc.create_volume(svc.VolumeIn(name="a", size_gb=5), USER, db)
    b = svc.create_volume(svc.VolumeIn(name="b", size_gb=1), USER, db)
    assert a["host"] != b["host"]
    cg = svc.ConsistencyGroup(name="cg", volume_ids=f'["{a["id"]}", "{b["id"]}"]', project_id="proj-1", status="available")
    db.add(cg)
    db.commit()
    with pytest.raises(svc.HTTPException) as exc:
        svc.create_cgsnapshot(svc.CGSnapshotIn(name="cgs", consistency_group_id=cg.id), USER, db)
    assert exc.value.status_code == 409
