"""Creating a volume from an image needs the right to read it.

block-store's real `create_volume` builds the headers (internal token + X-Caller-Claims); only `requests.get` is
replaced, by an adapter that hands the image URL to a real image-api TestClient. So block-store and image-api are
tested together: a refusal is image-api's own 404, passed through.
"""
import importlib
import io
import json

import pytest
import requests
from fastapi.testclient import TestClient
from shared import catalog
from shared.auth_middleware import require_auth, require_user_or_internal
from shared.internal import internal_headers
from tests.stack import fresh_service

TOKEN = "s3cret"
IMAGE_URL = "http://image.test"
OWNER = {"project_id": "p1", "role": "member", "roles": ["member"], "sub": "u-owner", "scope_type": "project"}
OTHER = {"project_id": "p2", "role": "member", "roles": ["member"], "sub": "u-other", "scope_type": "project"}
ACCEPTED = {"project_id": "p3", "role": "member", "roles": ["member"], "sub": "u-accepted", "scope_type": "project"}
PENDING = {"project_id": "p4", "role": "member", "roles": ["member"], "sub": "u-pending", "scope_type": "project"}
ADMIN = {"project_id": "admin", "role": "admin", "roles": ["admin"], "sub": "u-admin", "scope_type": "project"}
INTERNAL = {"sub": "internal", "username": "admin", "project_id": "admin", "role": "admin", "roles": ["admin"],
            "scope_type": "system", "jti": "internal"}  # what require_user_or_internal builds for the internal token


class Reply:
    def __init__(self, status_code, body):
        self.status_code, self._body = status_code, body
        self.text = json.dumps(body)

    def json(self):
        return self._body


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("INTERNAL_TOKEN", TOKEN)
    monkeypatch.setenv("REVOCATION_CHECK", "false")
    monkeypatch.setenv("IMAGE_API_URL", IMAGE_URL)
    monkeypatch.setenv("VOLUME_STORE_PATH", str(tmp_path / "volumes"))
    monkeypatch.setenv("STORAGE_LVM_DRIVER", "true")

    # the real image-api, on its own database
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'i.sqlite'}")
    monkeypatch.setenv("IMAGE_STORE_PATH", str(tmp_path / "store"))
    import services.image.app.main as image

    image = importlib.reload(image)
    image.Base.metadata.create_all(bind=image.engine)
    monkeypatch.setattr("shared.domain.domain_projects", lambda d: [])
    image_client = TestClient(image.app)

    def make_image(name, visibility="private", members=()):
        for dep in (require_auth, require_user_or_internal):
            image.app.dependency_overrides[dep] = lambda: OWNER  # only to create the image; cleared below
        r = image_client.post("/images", data={"name": name, "disk_format": "raw", "visibility": visibility},
                              files={"file": ("f.img", io.BytesIO(b"hello"))})
        image.app.dependency_overrides.clear()  # from here on, the real auth chain
        assert r.status_code == 201, r.text
        with image.SessionLocal() as db:
            for project, status in members:
                db.add(image.ImageMember(image_id=r.json()["id"], member_id=project, status=status))
            db.commit()
        return r.json()

    # block-store, on its own database, with everything but image-api stubbed
    svc = fresh_service("block_store", monkeypatch, tmp_path)
    who = {}
    svc.app.dependency_overrides[require_auth] = lambda: who
    seen = {"image_calls": [], "image_urls": [], "casts": []}
    peer = {"image": None}  # set by a test to make image-api misbehave: an exception to raise, or a Reply to return

    def fake_get(url, headers=None, timeout=None, **kw):
        if url.startswith(IMAGE_URL):
            seen["image_calls"].append(headers)
            seen["image_urls"].append(url)
            if isinstance(peer["image"], Exception):
                raise peer["image"]
            if peer["image"] is not None:
                return peer["image"]
            return image_client.get(url[len(IMAGE_URL):], headers=headers)
        if "/quota" in url:  # auth-api, not what is tested here: unreachable is skipped by the quota check
            raise requests.ConnectionError("auth-api is not part of this test")
        raise AssertionError(f"unexpected GET {url}")

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(svc.clients, "cast_to_worker", lambda method, payload: seen["casts"].append(payload))
    monkeypatch.setattr(svc.clients, "_rpc", type("Rpc", (), {"publish_event": lambda *a, **k: None})())
    db = svc.SessionLocal()
    db.add(svc.VolumeType(id="vt", name="default", backend="lvm", is_public=True))
    db.commit()
    db.close()

    def as_(identity):
        who.clear()
        who.update(identity)

    return type("Env", (), {"svc": svc, "client": TestClient(svc.app), "seen": seen, "peer": peer,
                            "make_image": staticmethod(make_image), "as_": staticmethod(as_)})


def create(env, image, name="v1"):
    return env.client.post("/volumes", json={"name": name, "size_gb": 1, "image": image})


def volume_rows(env):
    db = env.svc.SessionLocal()
    try:
        return db.query(env.svc.Volume).count()
    finally:
        db.close()


READABLE = {
    "public": (OTHER, "public", ()),
    "owned": (OWNER, "private", ()),
    "shared": (ACCEPTED, "private", [("p3", "accepted")]),
}
UNREADABLE = {
    "private_of_another_project": (OTHER, "private", ()),
    "pending_share": (PENDING, "private", [("p4", "pending")]),
}


def test_create_volume_refuses_an_unreadable_image(env):
    env.make_image("x", "private")
    env.as_(OTHER)

    r = create(env, "x")

    assert r.status_code == 404
    assert r.json() == {"detail": "image 'x' not found"}
    assert len(env.seen["image_calls"]) == 1
    assert volume_rows(env) == 0, "no Volume row, so nothing counts against the quota"
    assert env.seen["casts"] == [], "and nothing is sent to the worker"


@pytest.mark.parametrize("case", UNREADABLE)
def test_create_volume_refuses_every_unreadable_case(env, case):
    who, visibility, members = UNREADABLE[case]
    env.make_image("x", visibility, members)
    env.as_(who)

    r = create(env, "x")

    assert r.status_code == 404 and r.json() == {"detail": "image 'x' not found"}
    assert volume_rows(env) == 0 and env.seen["casts"] == []


def test_a_refused_image_leaves_the_quota_untouched(env):
    env.make_image("x", "private")
    env.as_(OTHER)
    db = env.svc.SessionLocal()
    db.add(env.svc.VolumeQuota(project_id="p2", volumes=1, gigabytes=1))
    db.commit()
    db.close()

    assert create(env, "x").status_code == 404
    # the one volume the quota allows is still available to a volume that does not use the unreadable image
    r = env.client.post("/volumes", json={"name": "blank", "size_gb": 1})
    assert r.status_code == 201, r.text


def test_the_image_is_judged_after_the_quota(env):
    env.make_image("x", "private")
    env.as_(OTHER)
    db = env.svc.SessionLocal()
    db.add(env.svc.VolumeQuota(project_id="p2", volumes=0, gigabytes=1))
    db.commit()
    db.close()

    r = create(env, "x")

    assert r.status_code == 413
    assert env.seen["image_calls"] == [], "an over-quota request does not reach image-api"


@pytest.mark.parametrize("case", READABLE)
def test_create_volume_from_a_readable_image_uses_the_resolved_id(env, case):
    who, visibility, members = READABLE[case]
    image = env.make_image("x", visibility, members)
    env.as_(who)

    r = create(env, "x")  # by name

    assert r.status_code == 201, r.text
    assert r.json()["image_id"] == image["id"] != "x"
    assert [c["image_ref"] for c in env.seen["casts"]] == [image["id"]]
    assert env.seen["casts"][0]["_image_api_url"] == IMAGE_URL
    sent = env.seen["image_calls"][0]
    assert sent["X-Caller-Claims"] and json.loads(sent["X-Caller-Claims"])["project_id"] == who["project_id"]
    assert {k: sent[k] for k in internal_headers()} == internal_headers()


def test_image_id_field_is_checked_too(env):
    env.make_image("x", "private")
    env.as_(OTHER)

    r = env.client.post("/volumes", json={"name": "v1", "size_gb": 1, "image_id": "x"})

    assert r.status_code == 404 and volume_rows(env) == 0 and env.seen["casts"] == []


def test_create_volume_without_an_image_does_not_call_image_api(env):
    env.as_(OTHER)

    r = env.client.post("/volumes", json={"name": "blank", "size_gb": 1})

    assert r.status_code == 201, r.text
    assert env.seen["image_calls"] == []
    assert r.json()["image_id"] is None


@pytest.mark.parametrize("who", [ADMIN, INTERNAL], ids=["admin", "internal"])
def test_admin_and_the_internal_caller_read_any_image(env, who):
    image = env.make_image("x", "private")
    env.as_(who)

    r = create(env, "x")

    assert r.status_code == 201, r.text
    assert r.json()["image_id"] == image["id"]
    assert [c["image_ref"] for c in env.seen["casts"]] == [image["id"]]


def test_the_internal_route_is_unchanged(env):
    env.make_image("x", "private")
    env.as_(OTHER)  # a member's claims; the internal route does not look at them

    r = env.client.post("/internal/volumes", headers=internal_headers(),
                        json={"name": "root", "size_gb": 1, "image_ref": "x", "project_id": "p2"})

    assert r.status_code == 201, r.text
    assert env.seen["image_calls"] == [], "an internal caller names the image it already resolved"
    assert [c["image_ref"] for c in env.seen["casts"]] == ["x"]


def test_a_catalog_failure_is_still_502(env, monkeypatch):
    monkeypatch.delenv("IMAGE_API_URL", raising=False)

    def down(service, interface="internal"):
        raise catalog.CatalogError(f"cannot resolve service '{service}'")

    monkeypatch.setattr(catalog, "url", down)
    env.as_(OTHER)

    r = create(env, "x")

    assert r.status_code == 502 and "image" in r.json()["detail"]
    assert volume_rows(env) == 0 and env.seen["casts"] == []


def test_an_unreachable_image_api_is_502(env):
    env.peer["image"] = requests.ConnectionError("connection refused")
    env.as_(OTHER)

    r = create(env, "x")

    assert r.status_code == 502 and "connection refused" in r.json()["detail"]
    assert volume_rows(env) == 0 and env.seen["casts"] == []


@pytest.mark.parametrize("status", [401, 403, 500, 503])
def test_an_image_api_that_fails_or_refuses_the_service_is_502(env, status):
    env.peer["image"] = Reply(status, {"detail": "boom"})
    env.as_(OTHER)

    r = create(env, "x")

    assert r.status_code == 502 and "boom" in r.json()["detail"]
    assert volume_rows(env) == 0 and env.seen["casts"] == []


def test_another_client_error_from_image_api_is_passed_through(env):
    env.peer["image"] = Reply(400, {"detail": "bad reference"})
    env.as_(OTHER)

    r = create(env, "x")

    assert r.status_code == 400 and r.json() == {"detail": "bad reference"}
    assert volume_rows(env) == 0


def test_a_ref_with_a_slash_never_addresses_another_image_api_route(env):
    env.make_image("x", "public")
    env.as_(OTHER)

    r = create(env, "x/download")

    assert r.status_code in (404, 502), r.text
    assert volume_rows(env) == 0 and env.seen["casts"] == []
    assert "x%2Fdownload" in env.seen["image_urls"][0]


@pytest.mark.parametrize("body", [{"name": "x"}, {"id": 5}, {"id": ""}, ["x"], None], ids=["no id", "id int", "id empty", "list", "null"])
def test_an_image_answer_without_a_usable_id_is_502(env, body):
    env.peer["image"] = Reply(200, body)
    env.as_(OTHER)

    r = create(env, "x")

    assert r.status_code == 502 and "without an id" in r.json()["detail"]
    assert volume_rows(env) == 0 and env.seen["casts"] == []


def test_an_image_answer_that_is_not_json_is_502(env):
    class Html(Reply):
        def json(self):
            raise ValueError("not json")

    env.peer["image"] = Html(200, "<html>")
    env.as_(OTHER)

    r = create(env, "x")

    assert r.status_code == 502 and volume_rows(env) == 0 and env.seen["casts"] == []


def test_claims_too_large_for_the_header_are_400_and_create_nothing(env):
    env.make_image("x", "public")
    env.as_({**OTHER, "roles": [f"role-{i:04d}" for i in range(400)]})

    r = create(env, "x")

    assert r.status_code == 400 and r.json() == {"detail": "caller identity is too large to send to image-api"}
    assert env.seen["image_calls"] == [] and volume_rows(env) == 0 and env.seen["casts"] == []


def test_with_internal_token_unset_image_api_reads_the_call_as_anonymous(env, monkeypatch):
    public = env.make_image("pub", "public")
    env.make_image("mine", "private")  # owned by OWNER's project
    monkeypatch.delenv("INTERNAL_TOKEN")
    env.as_(OWNER)

    ok = create(env, "pub", name="v1")
    refused = create(env, "mine", name="v2")

    assert ok.status_code == 201, ok.text
    assert ok.json()["image_id"] == public["id"]
    assert refused.status_code == 404 and refused.json() == {"detail": "image 'mine' not found"}
    assert all("X-Internal-Token" not in h and "X-Caller-Claims" not in h for h in env.seen["image_calls"])
    assert volume_rows(env) == 1 and len(env.seen["casts"]) == 1
