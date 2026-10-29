import pytest
from fastapi.testclient import TestClient
from shared.auth_middleware import require_auth
from tests.stack import fresh_service


@pytest.fixture
def api(monkeypatch, tmp_path):
    svc = fresh_service("block_store", monkeypatch, tmp_path)
    role = {"role": "admin"}
    svc.app.dependency_overrides[require_auth] = lambda: {"project_id": "proj-1", **role}
    with svc.SessionLocal() as db:
        db.add(svc.VolumeType(id="vt1", name="gold", backend="ceph", disk_type="nvme_ssd", max_iops=1000, is_public=True))
        db.commit()
    return svc, TestClient(svc.app), role


def test_patch_changes_the_mutable_fields_and_leaves_the_rest(api):
    # the CLI (`volume-type set`) and the dashboard both PATCH this route; it did not exist
    svc, client, _ = api
    r = client.patch("/volume-types/gold", json={"description": "fast", "max_iops": 5000, "is_public": False})
    assert r.status_code == 200
    body = r.json()
    assert (body["description"], body["max_iops"], body["is_public"]) == ("fast", 5000, False)
    assert (body["backend"], body["disk_type"], body["max_throughput"]) == ("ceph", "nvme_ssd", None)
    assert client.get("/volume-types/vt1").json()["max_iops"] == 5000


def test_fields_that_are_not_sent_are_not_reset(api):
    _, client, _ = api
    client.patch("/volume-types/gold", json={"max_throughput": 200})
    body = client.patch("/volume-types/gold", json={"description": "x"}).json()
    assert body["max_throughput"] == 200 and body["max_iops"] == 1000 and body["is_public"] is True


def test_a_field_can_be_cleared_with_null(api):
    _, client, _ = api
    assert client.patch("/volume-types/gold", json={"max_iops": None}).json()["max_iops"] is None


def test_backend_and_disk_type_cannot_be_changed(api):
    # existing volumes live on that backend; changing it would lie about where they are
    _, client, _ = api
    for payload in ({"backend": "lvm"}, {"disk_type": "sata_hdd"}, {"lvm_type": "thin"}, {"name": "other"}):
        assert client.patch("/volume-types/gold", json=payload).status_code == 422
    body = client.get("/volume-types/gold").json()
    assert (body["name"], body["backend"], body["disk_type"]) == ("gold", "ceph", "nvme_ssd")


def test_negative_limits_and_a_null_is_public_are_rejected(api):
    _, client, _ = api
    assert client.patch("/volume-types/gold", json={"max_iops": -1}).status_code == 422
    assert client.patch("/volume-types/gold", json={"is_public": None}).status_code == 422


def test_unknown_type_is_404_and_a_member_is_403(api):
    _, client, role = api
    assert client.patch("/volume-types/nope", json={"description": "x"}).status_code == 404
    role["role"] = "member"
    assert client.patch("/volume-types/gold", json={"description": "x"}).status_code == 403
    assert client.get("/volume-types/gold").json()["description"] is None
