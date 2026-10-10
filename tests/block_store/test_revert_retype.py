from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from tests.block_store import test_clone

USER, block = test_clone.USER, test_clone.block


def act(svc, db, ref, body):
    return svc.volume_action(ref, body, USER, db)


def test_revert_casts_to_worker_and_marks_reverting(block):
    svc, db, casts = block
    db.delete(db.get(svc.Snapshot, "s2"))
    db.commit()
    assert act(svc, db, "src", {"revert": {"snapshot_id": "snap"}})["status"] == "reverting"
    assert casts[-1] == ("revert_volume", {"volume_id": "v1", "volume_path": "/dev/vg/src", "snapshot_path": "/dev/vg/snap"})
    assert db.get(svc.Volume, "v1").status == "reverting"


def test_revert_only_to_latest_available_snapshot(block):
    svc, db, casts = block
    db.get(svc.Snapshot, "s1").created_at = datetime.utcnow() - timedelta(hours=1)
    db.get(svc.Snapshot, "s2").status = "available"
    db.commit()
    with pytest.raises(HTTPException) as e:
        act(svc, db, "src", {"revert": {"snapshot_id": "snap"}})
    assert e.value.status_code == 409 and casts == []


def test_retype_changes_type_but_not_backend(block):
    svc, db, _ = block
    db.add(svc.VolumeType(id="vt2", name="fast", backend="lvm", is_public=True))
    db.add(svc.VolumeType(id="vt3", name="ceph", backend="ceph", is_public=True))
    db.commit()
    assert act(svc, db, "src", {"os-retype": {"new_type": "fast"}})["volume_type"] == "fast"
    with pytest.raises(HTTPException) as e:
        act(svc, db, "src", {"os-retype": {"new_type": "ceph"}})
    assert e.value.status_code == 409
