"""Volume snapshot schedules belong to their volume's project: a schedule of a volume of another project is not found,
and a schedule whose volume is gone is deleted by an admin only.

The routes are driven through a TestClient with the real auth dependencies.
"""
import pytest
import requests
from fastapi.testclient import TestClient
from shared.auth_middleware import require_auth
from tests.stack import fresh_service

TOKEN = "s3cret"
OWNER = {"project_id": "p1", "role": "member", "roles": ["member"], "sub": "u-owner", "scope_type": "project"}
OTHER = {"project_id": "p2", "role": "member", "roles": ["member"], "sub": "u-other", "scope_type": "project"}
DOMAIN_ADMIN = {"project_id": "p9", "role": "member", "roles": ["member"], "sub": "u-da", "scope_type": "project",
                "domain_admin": True, "domain_id": "d1"}
ADMIN = {"project_id": "admin", "role": "admin", "roles": ["admin"], "sub": "u-admin", "scope_type": "project"}

BODY = {"interval_seconds": 7200, "retention_count": 3}
REFS = ["v1", "src"]


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("INTERNAL_TOKEN", TOKEN)
    monkeypatch.setenv("REVOCATION_CHECK", "false")
    monkeypatch.setenv("STORAGE_LVM_DRIVER", "true")
    monkeypatch.setattr("shared.domain.domain_projects", lambda d: ["p1"] if d == "d1" else [])
    svc = fresh_service("block_store", monkeypatch, tmp_path)
    who = {}
    svc.app.dependency_overrides[require_auth] = lambda: dict(who)

    def no_auth_api(url, **kw):
        raise requests.ConnectionError("auth-api is not part of this test")

    monkeypatch.setattr(requests, "get", no_auth_api)
    db = svc.SessionLocal()
    db.add(svc.Volume(id="v1", name="src", size_gb=2, status="available", path="/dev/vg/src", backend="lvm",
                      project_id="p1"))
    db.add(svc.VolumeSnapshotSchedule(id="s1", volume_id="v1", interval_seconds=3600, retention_count=5))
    db.add(svc.VolumeSnapshotSchedule(id="s-gone", volume_id="v-gone", interval_seconds=3600, retention_count=5))
    db.commit()
    db.close()

    def as_(identity):
        who.clear()
        who.update(identity)

    def schedule(sch_id="s1"):
        with svc.SessionLocal() as s:
            sch = s.get(svc.VolumeSnapshotSchedule, sch_id)
            return None if sch is None else (sch.interval_seconds, sch.retention_count)

    def count():
        with svc.SessionLocal() as s:
            return s.query(svc.VolumeSnapshotSchedule).count()

    as_(OWNER)
    return type("Env", (), {"svc": svc, "client": TestClient(svc.app), "as_": staticmethod(as_),
                            "schedule": staticmethod(schedule), "count": staticmethod(count)})


# ── schedules of a volume of another project ────────────────────────────────────

@pytest.mark.parametrize("ref", REFS)
def test_a_schedule_is_not_set_on_a_volume_of_another_project(env, ref):
    env.as_(OTHER)

    r = env.client.post(f"/volumes/{ref}/schedules", json=BODY)

    assert r.status_code == 404
    assert r.json() == {"detail": f"volume '{ref}' not found"}
    assert env.schedule() == (3600, 5), "the schedule is unchanged"
    assert env.count() == 2


@pytest.mark.parametrize("ref", REFS)
def test_a_schedule_of_a_volume_of_another_project_is_not_found(env, ref):
    env.as_(OTHER)

    r = env.client.get(f"/volumes/{ref}/schedules")

    assert r.status_code == 404
    assert r.json() == {"detail": f"volume '{ref}' not found"}


def test_a_schedule_of_a_volume_of_another_project_is_not_deleted(env):
    env.as_(OTHER)

    r = env.client.delete("/schedules/s1")

    assert r.status_code == 404
    assert r.json() == {"detail": "schedule not found"}
    assert env.schedule() == (3600, 5)


# ── the owner, the admin of the domain and an admin ─────────────────────────────

@pytest.mark.parametrize("who", [OWNER, DOMAIN_ADMIN, ADMIN], ids=["owner", "domain_admin", "admin"])
@pytest.mark.parametrize("ref", REFS)
def test_the_project_its_domain_admin_and_an_admin_set_and_read_a_schedule(env, who, ref):
    env.as_(who)

    r = env.client.post(f"/volumes/{ref}/schedules", json=BODY)

    assert r.status_code == 201
    assert env.schedule() == (7200, 3)
    r = env.client.get(f"/volumes/{ref}/schedules")
    assert r.status_code == 200
    assert r.json()["id"] == "s1"
    assert r.json()["retention_count"] == 3


@pytest.mark.parametrize("who", [OWNER, DOMAIN_ADMIN, ADMIN], ids=["owner", "domain_admin", "admin"])
def test_the_project_its_domain_admin_and_an_admin_delete_a_schedule(env, who):
    env.as_(who)

    r = env.client.delete("/schedules/s1")

    assert r.status_code == 204
    assert env.schedule() is None


def test_deleting_an_unknown_schedule_answers_204(env):
    env.as_(OTHER)

    r = env.client.delete("/schedules/nope")

    assert r.status_code == 204
    assert env.count() == 2


# ── a schedule whose volume is gone ─────────────────────────────────────────────

def test_a_schedule_without_its_volume_is_deleted_by_an_admin(env):
    env.as_(ADMIN)

    r = env.client.delete("/schedules/s-gone")

    assert r.status_code == 204
    assert env.schedule("s-gone") is None


@pytest.mark.parametrize("who", [OWNER, OTHER, DOMAIN_ADMIN], ids=["owner", "other", "domain_admin"])
def test_a_schedule_without_its_volume_is_not_deleted_by_a_non_admin(env, who):
    env.as_(who)

    r = env.client.delete("/schedules/s-gone")

    assert r.status_code == 404
    assert r.json() == {"detail": "schedule not found"}
    assert env.schedule("s-gone") == (3600, 5)
