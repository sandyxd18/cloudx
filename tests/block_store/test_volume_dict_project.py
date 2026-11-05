"""compute reads `project_id` from block-store's volume answer, so the serialized volume always carries the key."""
from tests.block_store import test_clone

block = test_clone.block


def test_the_volume_dict_carries_the_project_of_an_owned_volume(block):
    svc, db, _ = block

    assert svc.volume_to_os_dict(db.get(svc.Volume, "v1"))["project_id"] == "proj-1"


def test_the_volume_dict_carries_the_key_for_a_volume_without_an_owner(block):
    svc, db, _ = block
    db.add(svc.Volume(id="v9", name="free", size_gb=1, status="available", path="/dev/vg/free", backend="lvm"))
    db.commit()

    out = svc.volume_to_os_dict(db.get(svc.Volume, "v9"))

    assert "project_id" in out and not out["project_id"]
