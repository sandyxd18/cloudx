"""POST /volumes/{ref}/upload-image: snapshot a volume and hand the snapshot to the worker that owns it, which uploads it
as an image of the volume's project (compute-api's server snapshot uses it)."""
import pytest
from fastapi import HTTPException
from tests.block_store import test_clone

USER, block = test_clone.USER, test_clone.block


def test_it_snapshots_the_volume_and_asks_the_worker_to_upload_the_snapshot(block):
    svc, db, casts = block

    out = svc.upload_volume_image("src", svc.ImageUploadIn(image_name="web-backup", project_id="p7"), USER, db)

    method, payload = casts[-1]
    assert method == "create_snapshot" and payload["volume_id"] == "v1"
    assert payload["_upload_image"] == {"name": "web-backup", "project_id": "p7", "visibility": "private", "disk_format": "qcow2"}
    assert out["status"] == "creating" and out["name"] == "web-backup-root"


def test_the_image_belongs_to_the_volumes_project_unless_told_otherwise(block):
    svc, db, casts = block

    svc.upload_volume_image("src", svc.ImageUploadIn(image_name="web-backup"), USER, db)

    assert casts[-1][1]["_upload_image"]["project_id"] == "proj-1"


def test_a_plain_snapshot_carries_no_upload_request(block):
    svc, db, casts = block

    svc.create_volume_snapshot("src", svc.SnapshotIn(name="plain"), USER, db)

    assert "_upload_image" not in casts[-1][1]


def test_a_second_upload_with_the_same_name_while_the_first_runs_is_409(block):
    svc, db, casts = block
    svc.upload_volume_image("src", svc.ImageUploadIn(image_name="web-backup"), USER, db)

    with pytest.raises(HTTPException) as e:
        svc.upload_volume_image("src", svc.ImageUploadIn(image_name="web-backup"), USER, db)

    assert e.value.status_code == 409
