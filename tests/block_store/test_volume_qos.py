"""The limits of a volume's type (`max_iops`, `max_throughput`) reach the compute node: block-store puts them in the attach
call to compute-api and in the internal record the compute worker reads when it builds a server. They are read from the
type when the volume is attached, so a change to the type applies from the next attach (a Ceph volume keeps the limit it
was created with)."""
import importlib

import pytest
from tests.block_store import test_clone

block = test_clone.block
OWNER = {"project_id": "proj-1", "role": "admin", "sub": "u1"}


@pytest.fixture
def routes(block, monkeypatch):
    svc, db, _ = block
    volumes = importlib.import_module("services.block_store.app.routers.volumes")
    posts = []

    class Ok:
        status_code = 200
        text = ""

    monkeypatch.setattr(volumes.requests, "post", lambda url, **kw: posts.append(kw["json"]) or Ok())
    monkeypatch.setattr(volumes, "compute_api_url", lambda: "http://compute.test")
    monkeypatch.setattr(volumes, "server_locked", lambda server_id: False)
    monkeypatch.setattr(volumes, "get_server", lambda ref: {"id": "srv-1", "project_id": "proj-1", "status": "ACTIVE"})
    return volumes, svc, db, posts


def set_type(db, svc, **limits):
    vt = db.get(svc.VolumeType, "vt")
    for key, value in limits.items():
        setattr(vt, key, value)
    db.get(svc.Volume, "v1").volume_type = "default"
    db.commit()


def test_attach_sends_the_limits_of_the_volumes_type(routes):
    volumes, svc, db, posts = routes
    set_type(db, svc, max_iops=500, max_throughput=20)

    volumes.attach_volume("src", svc.AttachIn(server="srv-1"), OWNER, db)

    assert posts[0]["max_iops"] == 500 and posts[0]["max_throughput"] == 20


def test_the_limits_follow_the_type_at_the_next_attach(routes):
    volumes, svc, db, posts = routes
    set_type(db, svc, max_iops=500)
    volumes.attach_volume("src", svc.AttachIn(server="srv-1"), OWNER, db)
    volumes.detach_volume("src", OWNER, db)

    set_type(db, svc, max_iops=900)
    volumes.attach_volume("src", svc.AttachIn(server="srv-1"), OWNER, db)

    assert [p.get("max_iops") for p in posts if "target_dev" in p and p.get("max_iops") is not None] == [500, 900]


def test_a_volume_without_a_type_or_with_a_deleted_type_has_no_limits(routes):
    volumes, svc, db, posts = routes
    db.get(svc.Volume, "v1").volume_type = None
    db.commit()
    volumes.attach_volume("src", svc.AttachIn(server="srv-1"), OWNER, db)
    assert posts[0]["max_iops"] is None and posts[0]["max_throughput"] is None

    volumes.detach_volume("src", OWNER, db)
    db.get(svc.Volume, "v1").volume_type = "gone"
    db.commit()
    volumes.attach_volume("src", svc.AttachIn(server="srv-1"), OWNER, db)
    assert posts[-1]["max_iops"] is None and posts[-1]["max_throughput"] is None


def test_the_internal_record_the_compute_worker_reads_carries_the_limits(routes):
    volumes, svc, db, _ = routes
    set_type(db, svc, max_iops=700, max_throughput=30)

    record = volumes.get_volume_internal("src", db)

    assert record["max_iops"] == 700 and record["max_throughput"] == 30
    assert record["path"] == "/dev/vg/src"  # the fields it had are still there


def test_the_internal_record_has_null_limits_when_the_type_sets_none(routes):
    volumes, svc, db, _ = routes
    set_type(db, svc)

    record = volumes.get_volume_internal("src", db)

    assert record["max_iops"] is None and record["max_throughput"] is None
