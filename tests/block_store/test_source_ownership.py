"""A volume or snapshot used as the source of a clone belongs to the caller's project, and the owner of an image made from
a volume is the volume's project unless an admin names another one.

The routes are driven through a TestClient with the real auth dependencies; the worker casts are captured.
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
INTERNAL = {"X-Internal-Token": TOKEN}


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
    db.add(svc.VolumeType(id="vt", name="default", backend="lvm", is_public=True))
    db.add(svc.Volume(id="v1", name="src", size_gb=2, status="available", path="/dev/vg/src", backend="lvm",
                      project_id="p1"))
    db.add(svc.Volume(id="v0", name="unowned", size_gb=2, status="available", path="/dev/vg/unowned", backend="lvm",
                      project_id=None))
    db.add(svc.Snapshot(id="s1", name="snap", volume_id="v1", size_gb=2, status="available", path="/dev/vg/snap",
                        backend="lvm"))
    db.commit()
    db.close()

    def as_(identity):
        who.clear()
        who.update(identity)

    def rows():
        with svc.SessionLocal() as s:
            return s.query(svc.Volume).count()

    as_(OWNER)
    return type("Env", (), {"svc": svc, "client": TestClient(svc.app), "casts": casts, "as_": staticmethod(as_),
                            "rows": staticmethod(rows)})


SOURCES = {
    "volume_name": {"source_volume": "src"},
    "volume_id": {"source_volume": "v1"},
    "snapshot_name": {"snapshot": "snap"},
    "snapshot_id": {"snapshot": "s1"},
}


def clone(env, source, **kw):
    return env.client.post("/volumes", json={"name": "c", "size_gb": 2, **source}, **kw)


@pytest.mark.parametrize("source", SOURCES)
def test_a_source_of_another_project_is_refused(env, source):
    env.as_(OTHER)
    before = env.rows()

    r = clone(env, SOURCES[source])

    assert r.status_code == 404
    (ref,) = SOURCES[source].values()
    kind = "volume" if "source_volume" in SOURCES[source] else "snapshot"
    assert r.json() == {"detail": f"{kind} '{ref}' not found"}
    assert env.rows() == before, "no Volume row"
    assert env.casts == [], "nothing is sent to the worker"


def test_a_refused_source_leaves_the_quota_untouched(env):
    env.as_(OTHER)
    with env.svc.SessionLocal() as s:
        s.add(env.svc.VolumeQuota(project_id="p2", volumes=1, gigabytes=2))
        s.commit()

    assert clone(env, SOURCES["volume_name"]).status_code == 404
    assert clone(env, SOURCES["snapshot_name"]).status_code == 404

    assert env.client.post("/volumes", json={"name": "blank", "size_gb": 2}).status_code == 201


@pytest.mark.parametrize("source", SOURCES)
def test_the_owner_may_clone_its_source(env, source):
    r = clone(env, SOURCES[source])

    assert r.status_code == 201, r.text
    assert r.json()["project_id"] == "p1"
    method, payload = env.casts[-1]
    assert method == "create_volume" and (payload["source_path"] or payload["snapshot_path"])


@pytest.mark.parametrize("who", [DOMAIN_ADMIN, ADMIN], ids=["domain_admin", "admin"])
@pytest.mark.parametrize("source", SOURCES)
def test_an_admin_of_the_domain_or_the_cloud_may_clone_a_source(env, who, source):
    env.as_(who)

    r = clone(env, SOURCES[source])

    assert r.status_code == 201, r.text
    assert len(env.casts) == 1


def test_a_domain_admin_of_another_domain_is_refused(env):
    env.as_({**DOMAIN_ADMIN, "domain_id": "d2"})

    assert clone(env, SOURCES["volume_name"]).status_code == 404
    assert env.casts == []


def test_a_source_without_an_owner_stays_usable(env):
    env.as_(OTHER)

    r = clone(env, {"source_volume": "unowned"})

    assert r.status_code == 201, r.text


@pytest.mark.parametrize("source", SOURCES)
def test_the_internal_route_clones_any_source(env, source):
    body = {"name": "c", "size_gb": 2, "project_id": "p5"}
    if "source_volume" in SOURCES[source]:
        body["source_volume_id"] = SOURCES[source]["source_volume"]
    else:
        body["snapshot_id"] = SOURCES[source]["snapshot"]

    r = env.client.post("/internal/volumes", json=body, headers=INTERNAL)

    assert r.status_code == 201, r.text
    assert r.json()["project_id"] == "p5"
    assert len(env.casts) == 1


# ── the owner of an image made from a volume ───────────────────────────────────

def upload(env, **body):
    return env.client.post("/volumes/src/upload-image", json={"image_name": "img", **body})


def uploaded_image(env):
    method, payload = env.casts[-1]
    assert method == "create_snapshot"
    return payload["_upload_image"]


def test_a_member_cannot_name_the_image_owner(env):
    r = upload(env, project_id="p2")

    assert r.status_code == 202, r.text
    assert uploaded_image(env)["project_id"] == "p1"


def test_the_image_belongs_to_the_volumes_project_when_none_is_named(env):
    assert upload(env).status_code == 202

    assert uploaded_image(env)["project_id"] == "p1"


def test_an_admin_may_name_the_image_owner(env):
    env.as_(ADMIN)

    assert upload(env, project_id="p2").status_code == 202

    assert uploaded_image(env)["project_id"] == "p2"


def test_a_domain_admin_cannot_name_the_image_owner(env):
    env.as_(DOMAIN_ADMIN)

    assert upload(env, project_id="p2").status_code == 202

    assert uploaded_image(env)["project_id"] == "p1"


def test_the_internal_caller_may_name_the_image_owner(env):
    r = env.client.post("/volumes/src/upload-image", json={"image_name": "img", "project_id": "p2"}, headers=INTERNAL)

    assert r.status_code == 202, r.text
    assert uploaded_image(env)["project_id"] == "p2"


def test_the_internal_caller_without_a_project_uses_the_volumes_project(env):
    r = env.client.post("/volumes/src/upload-image", json={"image_name": "img"}, headers=INTERNAL)

    assert r.status_code == 202, r.text
    assert uploaded_image(env)["project_id"] == "p1"
