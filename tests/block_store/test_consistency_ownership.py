"""Consistency groups and their snapshots belong to a project: a group or cgsnapshot of another project is not found, and
the volumes put into a group are the caller's own.

The routes are driven through a TestClient with the real auth dependencies; the worker casts are captured.
"""
import json

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


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("INTERNAL_TOKEN", TOKEN)
    monkeypatch.setenv("REVOCATION_CHECK", "false")
    monkeypatch.setenv("STORAGE_LVM_DRIVER", "true")
    monkeypatch.setattr("shared.domain.domain_projects", lambda d: ["p1"] if d == "d1" else [])
    svc = fresh_service("block_store", monkeypatch, tmp_path)
    who = {}
    svc.app.dependency_overrides[require_auth] = lambda: dict(who)
    casts = []

    def no_auth_api(url, **kw):
        raise requests.ConnectionError("auth-api is not part of this test")

    monkeypatch.setattr(requests, "get", no_auth_api)
    monkeypatch.setattr(svc.clients, "cast_to_worker", lambda method, payload: casts.append((method, payload)))
    monkeypatch.setattr(svc.clients, "_rpc", type("Rpc", (), {"publish_event": lambda *a, **k: None})())
    db = svc.SessionLocal()
    db.add(svc.Volume(id="v1", name="src", size_gb=2, status="available", path="/dev/vg/src", backend="lvm",
                      project_id="p1"))
    db.add(svc.Volume(id="v2", name="src2", size_gb=2, status="available", path="/dev/vg/src2", backend="lvm",
                      project_id="p1"))
    db.add(svc.Volume(id="vx", name="foreign", size_gb=2, status="available", path="/dev/vg/foreign", backend="lvm",
                      project_id="p2"))
    db.add(svc.Volume(id="v0", name="unowned", size_gb=2, status="available", path="/dev/vg/unowned", backend="lvm",
                      project_id=None))
    db.add(svc.ConsistencyGroup(id="g1", name="cg1", status="available", project_id="p1",
                                volume_ids=json.dumps(["v1"])))
    db.add(svc.ConsistencyGroup(id="g0", name="cg0", status="available", project_id=None,
                                volume_ids=json.dumps(["v1"])))
    db.add(svc.ConsistencyGroup(id="gm", name="cgmixed", status="available", project_id="p1",
                                volume_ids=json.dumps(["v1", "vx"])))
    db.add(svc.CGSnapshot(id="c1", name="cgs1", consistency_group_id="g1", status="available", project_id="p1"))
    db.commit()
    db.close()

    def as_(identity):
        who.clear()
        who.update(identity)

    def group_ids(ref="g1"):
        with svc.SessionLocal() as s:
            cg = s.get(svc.ConsistencyGroup, ref)
            return None if cg is None else json.loads(cg.volume_ids)

    def count(model):
        with svc.SessionLocal() as s:
            return s.query(model).count()

    as_(OWNER)
    return type("Env", (), {"svc": svc, "client": TestClient(svc.app), "casts": casts, "as_": staticmethod(as_),
                            "group_ids": staticmethod(group_ids), "count": staticmethod(count)})


GROUP_REFS = ["g1", "cg1"]
SNAP_REFS = ["c1", "cgs1"]
ACTIONS = {
    "add_volumes": {"add_volumes": {"volumes": ["v2"]}},
    "remove_volumes": {"remove_volumes": {"volumes": ["v1"]}},
    "delete": {"delete": {}},
}


# ── groups of another project ───────────────────────────────────────────────────

@pytest.mark.parametrize("ref", GROUP_REFS)
def test_a_group_of_another_project_is_not_found(env, ref):
    env.as_(OTHER)

    r = env.client.get(f"/consistencygroups/{ref}")

    assert r.status_code == 404
    assert r.json() == {"detail": f"Consistency group '{ref}' not found"}


@pytest.mark.parametrize("action", ACTIONS)
@pytest.mark.parametrize("ref", GROUP_REFS)
def test_an_action_on_a_group_of_another_project_changes_nothing(env, ref, action):
    env.as_(OTHER)

    r = env.client.post(f"/consistencygroups/{ref}/action", json=ACTIONS[action])

    assert r.status_code == 404
    assert r.json() == {"detail": f"Consistency group '{ref}' not found"}
    assert env.group_ids() == ["v1"]


@pytest.mark.parametrize("ref", GROUP_REFS)
def test_deleting_a_group_of_another_project_is_refused(env, ref):
    env.as_(OTHER)

    r = env.client.delete(f"/consistencygroups/{ref}")

    assert r.status_code == 404
    assert env.group_ids() == ["v1"]


@pytest.mark.parametrize("ref", GROUP_REFS)
def test_a_cgsnapshot_of_a_group_of_another_project_is_not_created(env, ref):
    env.as_(OTHER)
    before = env.count(env.svc.CGSnapshot)

    r = env.client.post("/cgsnapshots", json={"name": "n", "consistency_group_id": ref})

    assert r.status_code == 404
    assert r.json() == {"detail": f"Consistency group '{ref}' not found"}
    assert env.count(env.svc.CGSnapshot) == before, "no CGSnapshot row"
    assert env.casts == [], "nothing is sent to the worker"


@pytest.mark.parametrize("ref", SNAP_REFS)
def test_a_cgsnapshot_of_another_project_is_not_found(env, ref):
    env.as_(OTHER)

    r = env.client.get(f"/cgsnapshots/{ref}")

    assert r.status_code == 404
    assert r.json() == {"detail": f"CG snapshot '{ref}' not found"}


@pytest.mark.parametrize("ref", SNAP_REFS)
def test_deleting_a_cgsnapshot_of_another_project_is_refused(env, ref):
    env.as_(OTHER)

    r = env.client.delete(f"/cgsnapshots/{ref}")

    assert r.status_code == 404
    assert r.json() == {"detail": f"CG snapshot '{ref}' not found"}
    assert env.count(env.svc.CGSnapshot) == 1


# ── the owner, the domain admin and the admin ──────────────────────────────────

@pytest.mark.parametrize("who", [OWNER, DOMAIN_ADMIN, ADMIN], ids=["owner", "domain_admin", "admin"])
@pytest.mark.parametrize("ref", GROUP_REFS)
def test_the_group_is_visible_to_its_project_and_to_admins(env, who, ref):
    env.as_(who)

    r = env.client.get(f"/consistencygroups/{ref}")

    assert r.status_code == 200, r.text
    assert r.json()["id"] == "g1"


@pytest.mark.parametrize("who", [OWNER, DOMAIN_ADMIN, ADMIN], ids=["owner", "domain_admin", "admin"])
def test_the_group_can_be_changed_by_its_project_and_by_admins(env, who):
    env.as_(who)

    r = env.client.post("/consistencygroups/cg1/action", json=ACTIONS["add_volumes"])
    assert r.status_code == 200, r.text
    assert sorted(env.group_ids()) == ["v1", "v2"]

    r = env.client.post("/consistencygroups/cg1/action", json=ACTIONS["remove_volumes"])
    assert r.status_code == 200, r.text
    assert env.group_ids() == ["v2"]

    r = env.client.delete("/consistencygroups/g1")
    assert r.status_code == 202, r.text
    assert env.group_ids() is None


@pytest.mark.parametrize("who", [OWNER, DOMAIN_ADMIN, ADMIN], ids=["owner", "domain_admin", "admin"])
def test_the_group_can_be_deleted_by_action_by_its_project_and_by_admins(env, who):
    env.as_(who)

    r = env.client.post("/consistencygroups/g1/action", json=ACTIONS["delete"])

    assert r.status_code == 200, r.text
    assert env.group_ids() is None


@pytest.mark.parametrize("who", [OWNER, DOMAIN_ADMIN, ADMIN], ids=["owner", "domain_admin", "admin"])
@pytest.mark.parametrize("ref", SNAP_REFS)
def test_the_cgsnapshot_is_visible_and_removable_by_its_project_and_by_admins(env, who, ref):
    env.as_(who)

    assert env.client.get(f"/cgsnapshots/{ref}").status_code == 200

    assert env.client.delete(f"/cgsnapshots/{ref}").status_code == 202
    assert env.count(env.svc.CGSnapshot) == 0


@pytest.mark.parametrize("who", [OWNER, DOMAIN_ADMIN, ADMIN], ids=["owner", "domain_admin", "admin"])
def test_a_cgsnapshot_can_be_made_by_the_project_and_by_admins(env, who):
    env.as_(who)

    r = env.client.post("/cgsnapshots", json={"name": "n", "consistency_group_id": "cg1"})

    assert r.status_code == 201, r.text
    assert env.count(env.svc.CGSnapshot) == 2
    assert [m for m, _ in env.casts] == ["create_cgsnapshot"]


@pytest.mark.parametrize("ref", ["g0", "cg0"])
def test_a_group_without_a_project_is_for_admins_only(env, ref):
    env.as_(OTHER)
    assert env.client.get(f"/consistencygroups/{ref}").status_code == 404

    env.as_(ADMIN)
    r = env.client.get(f"/consistencygroups/{ref}")

    assert r.status_code == 200, r.text
    assert r.json()["id"] == "g0"


# ── the volumes of a group ─────────────────────────────────────────────────────

def test_a_group_is_not_created_with_a_volume_of_another_project(env):
    before = env.count(env.svc.ConsistencyGroup)

    r = env.client.post("/consistencygroups", json={"name": "new", "volume_ids": ["v1", "foreign"]})

    assert r.status_code == 404
    assert r.json() == {"detail": "volume 'foreign' not found"}
    assert env.count(env.svc.ConsistencyGroup) == before, "no group row"


def test_a_group_is_created_with_the_volumes_of_the_callers_project(env):
    r = env.client.post("/consistencygroups", json={"name": "new", "volume_ids": ["v1", "src2"]})

    assert r.status_code == 201, r.text
    assert r.json()["volume_ids"] == ["v1", "v2"]  # names are stored as the volumes' ids


def test_a_volume_of_another_project_is_not_added_to_a_group(env):
    for ref in ("vx", "foreign"):
        r = env.client.post("/consistencygroups/g1/action", json={"add_volumes": {"volumes": ["v2", ref]}})

        assert r.status_code == 404
        assert r.json() == {"detail": f"volume '{ref}' not found"}
        assert env.group_ids() == ["v1"], "the group is unchanged, including the volume that could have been added"


@pytest.mark.parametrize("ref", ["v2", "src2"])
def test_a_volume_of_the_callers_project_is_added_to_a_group(env, ref):
    r = env.client.post("/consistencygroups/g1/action", json={"add_volumes": {"volumes": [ref]}})

    assert r.status_code == 200, r.text
    assert len(env.group_ids()) == 2


def test_an_admin_may_put_a_volume_of_any_project_in_a_group(env):
    env.as_(ADMIN)

    r = env.client.post("/consistencygroups/g1/action", json={"add_volumes": {"volumes": ["vx"]}})

    assert r.status_code == 200, r.text
    assert sorted(env.group_ids()) == ["v1", "vx"]


# ── a snapshot of a group holds only volumes the caller may see ─────────────────

def test_a_cgsnapshot_is_refused_when_the_group_holds_a_volume_outside_the_callers_project(env):
    before = env.count(env.svc.CGSnapshot)

    r = env.client.post("/cgsnapshots", json={"name": "n", "consistency_group_id": "cgmixed"})

    assert r.status_code == 409
    assert r.json() == {"detail": "consistency group has volumes outside your project"}
    assert env.count(env.svc.CGSnapshot) == before, "no CGSnapshot row"
    assert env.casts == [], "nothing is sent to the worker"


def test_an_admin_may_snapshot_a_group_with_volumes_of_several_projects(env):
    env.as_(ADMIN)

    r = env.client.post("/cgsnapshots", json={"name": "n", "consistency_group_id": "cgmixed"})

    assert r.status_code == 201, r.text
    assert [m for m, _ in env.casts] == ["create_cgsnapshot"]


# ── lists are unchanged ────────────────────────────────────────────────────────

def test_a_member_lists_only_its_own_groups_and_snapshots(env):
    assert {g["id"] for g in env.client.get("/consistencygroups").json()["consistencygroups"]} == {"g1", "gm"}
    assert [s["id"] for s in env.client.get("/cgsnapshots").json()["cgsnapshots"]] == ["c1"]

    env.as_(OTHER)

    assert env.client.get("/consistencygroups").json()["consistencygroups"] == []
    assert env.client.get("/cgsnapshots").json()["cgsnapshots"] == []


def test_a_volume_is_removed_from_a_group_by_name_as_well_as_by_id(env):
    env.as_(OWNER)
    env.client.post("/consistencygroups/cg1/action", json=ACTIONS["add_volumes"])
    assert sorted(env.group_ids()) == ["v1", "v2"]

    r = env.client.post("/consistencygroups/cg1/action", json={"remove_volumes": {"volumes": ["src"]}})

    assert r.status_code == 200, r.text
    assert env.group_ids() == ["v2"]


@pytest.mark.parametrize("who", [DOMAIN_ADMIN, ADMIN], ids=["domain_admin", "admin"])
def test_a_cgsnapshot_made_by_an_admin_belongs_to_the_groups_project(env, who):
    env.as_(who)
    r = env.client.post("/cgsnapshots", json={"name": "by-admin", "consistency_group_id": "cg1"})
    assert r.status_code == 201, r.text

    assert r.json()["project_id"] == "p1"
    env.as_(OWNER)
    assert env.client.get("/cgsnapshots/by-admin").status_code == 200


# ── rows without a project are for admins and services only ────────────────────

NOT_ADMIN = [OWNER, OTHER, DOMAIN_ADMIN]


@pytest.mark.parametrize("who", NOT_ADMIN)
def test_a_group_without_a_project_is_not_found(env, who):
    env.as_(who)

    assert env.client.get("/consistencygroups/g0").status_code == 404
    assert env.client.post("/consistencygroups/g0/action", json={"delete": {}}).status_code == 404
    with env.svc.SessionLocal() as s:
        assert s.get(env.svc.ConsistencyGroup, "g0") is not None


@pytest.mark.parametrize("who", NOT_ADMIN)
def test_a_volume_without_a_project_is_not_found(env, who):
    env.as_(who)

    assert env.client.get("/volumes/v0").status_code == 404
    assert env.client.get("/volumes/unowned").status_code == 404


def test_an_admin_still_reaches_rows_without_a_project(env):
    env.as_(ADMIN)

    assert env.client.get("/consistencygroups/g0").status_code == 200
    assert env.client.get("/volumes/v0").status_code == 200


def test_a_volume_without_a_project_is_not_put_in_a_group_by_a_member(env):
    r = env.client.post("/consistencygroups/g1/action", json={"add_volumes": {"volumes": ["unowned"]}})

    assert r.status_code == 404
    assert env.group_ids() == ["v1"]
