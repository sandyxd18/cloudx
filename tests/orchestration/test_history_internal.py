"""Scaling history is recorded by the orchestration worker only.

`POST /scaling-groups/{id}/history` is driven through a TestClient with the real auth dependencies: a user JWT
(stubbed at `require_auth`), the internal token alone, and the internal token with a caller's claims.
"""
import importlib

import pytest
from fastapi.testclient import TestClient
from shared.auth_middleware import require_auth
from shared.internal import on_behalf_headers

TOKEN = "s3cret"
MEMBER = {"project_id": "p1", "role": "member", "roles": ["member"], "sub": "u-member", "scope_type": "project"}
ADMIN = {"project_id": "admin", "role": "admin", "roles": ["admin"], "sub": "u-admin", "scope_type": "project"}
WORKER = {"sub": "autoscale:g1", "username": "autoscale", "role": "member", "roles": ["member"],
          "project_id": "p1", "scope_type": "project"}


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'orch.sqlite'}")
    monkeypatch.setenv("INTERNAL_TOKEN", TOKEN)
    monkeypatch.setenv("REVOCATION_CHECK", "false")
    import services.orchestration.app.main as m

    m = importlib.reload(m)
    importlib.reload(m.stacks)
    m.Base.metadata.create_all(bind=m.engine)
    with m.SessionLocal() as db:
        db.add(m.ScalingGroup(id="g1", project_id="p1", name="web", launch_template_id="default", image_id="i", flavor_id="f", network_id="n"))
        db.add(m.ScalingGroupInstance(group_id="g1", server_id="srv-old", server_name="old", status="active"))
        db.commit()
    who = {}
    m.app.dependency_overrides[require_auth] = lambda: dict(who)

    def as_(identity):
        who.clear()
        who.update(identity)

    def rows():
        with m.SessionLocal() as s:
            instances = sorted(i.server_id for i in s.query(m.ScalingGroupInstance).all())
            return instances, s.query(m.ScalingHistory).count()

    as_(MEMBER)
    return type("Env", (), {"m": m, "client": TestClient(m.app), "as_": staticmethod(as_),
                            "rows": staticmethod(rows)})


def event(action="scale_out", server_id="srv-new"):
    return {"action": action, "reason": "cpu", "instance_count_before": 1, "instance_count_after": 2,
            "server_id": server_id, "server_name": "new"}


@pytest.mark.parametrize("path", ["/scaling-groups/g1/history", "/orchestration/scaling-groups/g1/history"])
@pytest.mark.parametrize("who", [MEMBER, ADMIN], ids=["member", "admin"])
def test_a_user_does_not_record_scaling_history(env, who, path):
    env.as_(who)

    r = env.client.post(path, json=event())

    assert r.status_code == 403
    assert r.json() == {"detail": "internal callers only"}
    assert env.rows() == (["srv-old"], 0), "no instance and no history row is added"


def test_a_user_does_not_remove_an_instance_row_through_history(env):
    r = env.client.post("/scaling-groups/g1/history", json=event("scale_in", "srv-old"))

    assert r.status_code == 403
    assert env.rows() == (["srv-old"], 0)


def test_the_internal_token_alone_records_a_scale_out(env):
    r = env.client.post("/scaling-groups/g1/history", json=event(), headers={"X-Internal-Token": TOKEN})

    assert r.status_code == 201
    assert r.json() == {"status": "recorded"}
    assert env.rows() == (["srv-new", "srv-old"], 1)


def test_the_internal_token_alone_records_a_scale_in(env):
    r = env.client.post("/scaling-groups/g1/history", json=event("scale_in", "srv-old"),
                        headers={"X-Internal-Token": TOKEN})

    assert r.status_code == 201
    assert env.rows() == ([], 1)


def test_the_internal_token_with_a_members_claims_does_not_record(env):
    r = env.client.post("/scaling-groups/g1/history", json=event(), headers=on_behalf_headers(MEMBER))

    assert r.status_code == 403
    assert env.rows() == (["srv-old"], 0)


def test_the_internal_token_with_the_workers_group_claims_does_not_record(env):
    r = env.client.post("/scaling-groups/g1/history", json=event(), headers=on_behalf_headers(WORKER))

    assert r.status_code == 403
    assert env.rows() == (["srv-old"], 0)


def test_a_missing_group_is_still_not_found_for_the_internal_caller(env):
    r = env.client.post("/scaling-groups/nope/history", json=event(), headers={"X-Internal-Token": TOKEN})

    assert r.status_code == 404


def test_the_owner_still_reads_history_and_instances(env):
    env.client.post("/scaling-groups/g1/history", json=event(), headers={"X-Internal-Token": TOKEN})

    history = env.client.get("/scaling-groups/g1/history")
    instances = env.client.get("/scaling-groups/g1/instances")

    assert history.status_code == 200
    assert [h["action"] for h in history.json()] == ["scale_out"]
    assert instances.status_code == 200
    assert sorted(i["server_id"] for i in instances.json()) == ["srv-new", "srv-old"]
