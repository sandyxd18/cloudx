import pytest
from fastapi import HTTPException
from tests.block_store import test_clone

USER, block = test_clone.USER, test_clone.block


def test_delete_refuses_a_volume_that_still_has_snapshots(block):
    # deleting it anyway removed the database row and left the backend volume (an RBD image with its snapshot) behind for good
    svc, db, casts = block
    with pytest.raises(HTTPException) as e:
        svc.delete_volume("src", USER, db)
    assert e.value.status_code == 409 and "snapshot" in e.value.detail
    assert db.get(svc.Volume, "v1").status == "available"
    assert casts == []


def test_delete_goes_ahead_once_the_snapshots_are_gone(block):
    svc, db, casts = block
    for snapshot_id in ("s1", "s2"):
        db.delete(db.get(svc.Snapshot, snapshot_id))
    db.commit()

    assert svc.delete_volume("src", USER, db)["status"] == "deleting"
    assert casts[-1][0] == "delete_volume"


def test_the_internal_delete_a_server_makes_also_refuses_a_volume_with_snapshots(block):
    # deleting a server deletes its root volume through this route; the guard above did not cover it, so the row went
    # and the RBD image and its snapshot stayed (on LVM the snapshot record went to `error`)
    svc, db, casts = block
    with pytest.raises(HTTPException) as e:
        svc.delete_volume_internal("src", db)
    assert e.value.status_code == 409 and "snapshot" in e.value.detail
    assert db.get(svc.Volume, "v1").status == "available"
    assert casts == []


def test_the_internal_delete_goes_ahead_once_the_snapshots_are_gone(block):
    svc, db, casts = block
    for snapshot_id in ("s1", "s2"):
        db.delete(db.get(svc.Snapshot, snapshot_id))
    db.commit()

    assert svc.delete_volume_internal("src", db)["status"] == "deleting"
    assert casts[-1][0] == "delete_volume"
