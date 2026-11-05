"""Booting from, rebuilding or rescuing with an image needs the right to read it.

compute's real `clients.api_get` builds the headers (internal token + X-Caller-Claims); only `requests.get` is
replaced, by an adapter that hands the image URL to a real image-api TestClient. So compute and image-api are
tested together: a refusal is image-api's own 404, passed through.
"""
import importlib
import io
import json

import pytest
import requests
from fastapi.testclient import TestClient
from shared.auth_middleware import require_auth, require_user_or_internal
from shared.internal import internal_headers, on_behalf_headers
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
PORT = {"id": "port1", "name": "lp-main", "mac_address": "02:00:00:00:00:01", "fixed_ip": "10.10.0.5"}


class Reply:
    def __init__(self, status_code, body):
        self.status_code, self._body = status_code, body
        self.text = json.dumps(body)

    def json(self):
        return self._body

    def raise_for_status(self):
        assert self.status_code < 400


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("INTERNAL_TOKEN", TOKEN)
    monkeypatch.setenv("REVOCATION_CHECK", "false")
    monkeypatch.setenv("IMAGE_API_URL", IMAGE_URL)
    for key in ("NETWORKING_API_URL", "BLOCK_STORE_API_URL", "PLACEMENT_API_URL"):
        monkeypatch.setenv(key, f"http://{key.split('_')[0].lower()}.test")

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

    # compute, on its own database, with everything but image-api stubbed
    svc = fresh_service("compute", monkeypatch, tmp_path)
    who = {}
    svc.app.dependency_overrides[require_auth] = lambda: who
    seen = {"image_calls": [], "image_urls": [], "claims": [], "released": [], "casts": [], "block_posts": [], "block_deletes": [],
            "ports": [], "api_deletes": []}

    def fake_get(url, headers=None, timeout=None, **kw):
        if url.startswith(IMAGE_URL):
            seen["image_calls"].append(headers)
            seen["image_urls"].append(url)
            return image_client.get(url[len(IMAGE_URL):], headers=headers)
        if "/networks/" in url:
            return Reply(200, {"id": "net1", "name": "private-net"})
        if "/ports/" in url:
            return Reply(200, PORT)
        raise AssertionError(f"unexpected GET {url}")

    def fake_post(url, headers=None, json=None, timeout=None, **kw):  # placement claim
        assert url.endswith("/internal/claim"), url
        seen["claims"].append(json["consumer_uuid"])
        return Reply(200, {"host": "node-1"})

    def fake_delete(url, headers=None, timeout=None, **kw):  # placement release
        assert "/internal/allocations/" in url, url
        seen["released"].append(url.rsplit("/", 1)[1])
        return Reply(200, {})

    servers = importlib.import_module("services.compute.app.routers.servers")
    monkeypatch.setattr(servers, "_check_project_quota", lambda *a, **k: None)  # asks auth-api, not what is tested here
    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(requests, "delete", fake_delete)
    monkeypatch.setattr(svc.clients, "cast_to_worker", lambda method, payload: seen["casts"].append(method))
    monkeypatch.setattr(svc.clients, "api_post", lambda url, payload, caller=None: seen["ports"].append(url) or {**PORT, "id": "port9"})
    monkeypatch.setattr(svc.clients, "api_delete", lambda url, payload=None: seen["api_deletes"].append(url))
    monkeypatch.setattr(svc.clients, "block_delete", lambda path: seen["block_deletes"].append(path))
    monkeypatch.setattr(svc.clients, "block_post", lambda path, payload: seen["block_posts"].append(path) or
                        {"id": "root1", "size_gb": 8, "status": "available", "path": "/volumes/root1.qcow2"})
    monkeypatch.setattr(svc.clients, "_rpc", type("Rpc", (), {"publish_event": lambda *a, **k: None})())
    db = svc.SessionLocal()
    db.add(svc.Flavor(name="small", vcpus=1, ram_mb=512, disk_gb=1))
    db.commit()
    db.close()

    def as_(identity):
        who.clear()
        who.update(identity)

    return type("Env", (), {"svc": svc, "client": TestClient(svc.app), "seen": seen,
                            "make_image": staticmethod(make_image), "as_": staticmethod(as_)})


def create(env, image):
    return env.client.post("/servers", json={"name": "vm1", "flavor": "small", "network": "net1", "image": image})


def add_server(env, project):
    db = env.svc.SessionLocal()
    db.add(env.svc.Server(id="srv1", name="vm1", project_id=project, image_id="img0", root_volume_id="vol1",
                          flavor="small", network_id="net1", port_id="port1", host="node-1", status="ACTIVE"))
    db.commit()
    db.close()


def server_status(env):
    db = env.svc.SessionLocal()
    try:
        return db.get(env.svc.Server, "srv1").status
    finally:
        db.close()


def server_rows(env):
    db = env.svc.SessionLocal()
    try:
        return db.query(env.svc.Server).count()
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


def test_create_server_refuses_an_image_the_caller_cannot_read(env):
    env.make_image("x", "private")
    env.as_(OTHER)

    r = create(env, "x")

    assert r.status_code == 404
    assert r.json() == {"detail": "image 'x' not found"}
    seen = env.seen
    assert len(seen["claims"]) == 1, "the placement claim is made before the image is looked up"
    assert seen["released"] == seen["claims"], "and released when the image is refused"
    assert server_rows(env) == 0
    assert seen["block_posts"] == [] and seen["block_deletes"] == []
    assert seen["ports"] == [] and seen["api_deletes"] == []
    assert seen["casts"] == []


@pytest.mark.parametrize("case", UNREADABLE)
def test_create_server_refuses_every_unreadable_case(env, case):
    who, visibility, members = UNREADABLE[case]
    env.make_image("x", visibility, members)
    env.as_(who)

    r = create(env, "x")

    assert r.status_code == 404 and r.json() == {"detail": "image 'x' not found"}
    assert server_rows(env) == 0 and env.seen["casts"] == [] and env.seen["block_posts"] == []
    assert env.seen["released"] == env.seen["claims"]


@pytest.mark.parametrize("case", READABLE)
def test_create_server_accepts_public_owned_and_shared_images(env, case):
    who, visibility, members = READABLE[case]
    env.make_image("x", visibility, members)
    env.as_(who)

    r = create(env, "x")

    assert r.status_code == 201, r.text
    assert r.json()["status"] == "BUILD"
    assert server_rows(env) == 1
    assert env.seen["block_posts"] == ["/internal/volumes"]
    assert env.seen["casts"] == ["build_instance"]
    assert env.seen["released"] == [], "a server that is built keeps its placement claim"


@pytest.mark.parametrize("who", [ADMIN, INTERNAL], ids=["admin", "internal"])
def test_admin_and_the_internal_caller_read_any_image(env, who):
    env.make_image("x", "private")
    env.as_(who)

    r = create(env, "x")

    assert r.status_code == 201, r.text
    assert env.seen["casts"] == ["build_instance"]


@pytest.mark.parametrize("who", [ADMIN, INTERNAL], ids=["admin", "internal"])
def test_rebuild_and_rescue_by_admin_and_the_internal_caller_read_any_image(env, who):
    env.make_image("x", "private")
    add_server(env, "p2")
    env.as_(who)

    assert env.client.post("/servers/srv1/rebuild", json={"image": "x"}).status_code == 200
    assert env.client.post("/servers/srv1/rescue", json={"image": "x"}).status_code == 200


def test_rebuild_refuses_an_unreadable_image_and_changes_nothing(env):
    image = env.make_image("x", "private")
    add_server(env, "p2")
    env.as_(OTHER)

    r = env.client.post("/servers/srv1/rebuild", json={"image": "x"})

    assert r.status_code == 404 and r.json() == {"detail": "image 'x' not found"}
    assert server_status(env) == "ACTIVE"
    db = env.svc.SessionLocal()
    assert db.get(env.svc.Server, "srv1").image_id == "img0" != image["id"]
    db.close()
    assert env.seen["casts"] == []


def test_rescue_refuses_an_unreadable_image(env):
    env.make_image("x", "private")
    add_server(env, "p2")
    env.as_(OTHER)

    r = env.client.post("/servers/srv1/rescue", json={"image": "x"})

    assert r.status_code == 404 and r.json() == {"detail": "image 'x' not found"}
    assert server_status(env) == "ACTIVE"
    assert env.seen["casts"] == []


@pytest.mark.parametrize("case", READABLE)
def test_rebuild_and_rescue_accept_a_readable_image(env, case):
    who, visibility, members = READABLE[case]
    image = env.make_image("x", visibility, members)
    add_server(env, who["project_id"])
    env.as_(who)

    r = env.client.post("/servers/srv1/rebuild", json={"image": "x"})
    assert r.status_code == 200, r.text
    assert r.json()["image_id"] == image["id"]
    assert env.seen["casts"] == ["rebuild"]

    db = env.svc.SessionLocal()
    db.get(env.svc.Server, "srv1").status = "ACTIVE"
    db.commit()
    db.close()
    r = env.client.post("/servers/srv1/rescue", json={"image": "x"})
    assert r.status_code == 200, r.text
    assert env.seen["casts"] == ["rebuild", "rescue"]


def test_api_get_sends_the_claims_header_only_when_a_caller_is_given(monkeypatch, tmp_path):
    monkeypatch.setenv("INTERNAL_TOKEN", TOKEN)
    svc = fresh_service("compute", monkeypatch, tmp_path)
    sent = []

    def fake_get(url, headers=None, timeout=None, **kw):
        sent.append(headers)
        return Reply(200, {"id": "i1"})

    monkeypatch.setattr(requests, "get", fake_get)

    svc.clients.api_get("http://peer.test/images/x")
    svc.clients.api_get("http://peer.test/images/x", caller=OTHER)
    svc.clients.api_get("http://peer.test/images/x", caller=INTERNAL)

    assert sent[0] == {"Content-Type": "application/json", **internal_headers()}
    assert "X-Caller-Claims" not in sent[0]
    assert sent[1] == {"Content-Type": "application/json", **on_behalf_headers(OTHER)}
    assert json.loads(sent[1]["X-Caller-Claims"])["project_id"] == "p2"
    assert sent[2] == {"Content-Type": "application/json", **internal_headers()}, "the internal caller sends no claims"


def test_create_server_with_claims_too_large_for_the_header_is_400_and_creates_nothing(env):
    env.make_image("x", "public")
    env.as_({**OTHER, "roles": [f"role-{i:04d}" for i in range(400)]})

    r = create(env, "x")

    assert r.status_code == 400 and r.json() == {"detail": "caller identity cannot be sent to a peer service"}
    seen = env.seen
    assert seen["released"] == seen["claims"], "no placement claim is left (the network is looked up before it is made)"
    assert seen["image_calls"] == []
    assert server_rows(env) == 0
    assert seen["block_posts"] == [] and seen["ports"] == [] and seen["casts"] == []


@pytest.mark.parametrize("action", ["create", "rebuild", "rescue"])
@pytest.mark.parametrize("ref, segment", [
    ("../images?name=x", "..%2Fimages%3Fname%3Dx"),
    ("a/b", "a%2Fb"),
    ("x?y#z", "x%3Fy%23z"),
    ("plain-name", "plain-name"),
    ("0b6f6a62-5a8e-4d3c-9d58-123456789abc", "0b6f6a62-5a8e-4d3c-9d58-123456789abc"),
])
def test_the_image_reference_is_one_encoded_path_segment(env, action, ref, segment):
    env.as_(OTHER)
    if action == "create":
        create(env, ref)
    else:
        add_server(env, "p2")
        env.client.post(f"/servers/srv1/{action}", json={"image": ref})

    assert env.seen["image_urls"][0] == f"{IMAGE_URL}/images/{segment}"


def test_a_plain_name_and_an_id_still_resolve(env):
    image = env.make_image("x", "public")
    env.as_(OTHER)

    assert create(env, "x").status_code == 201
    assert env.seen["image_urls"][0] == f"{IMAGE_URL}/images/x"
    env.seen["image_urls"].clear()
    r = env.client.post("/servers", json={"name": "vm2", "flavor": "small", "network": "net1", "image": image["id"]})
    assert r.status_code == 201, r.text
    assert env.seen["image_urls"][0] == f"{IMAGE_URL}/images/{image['id']}"
