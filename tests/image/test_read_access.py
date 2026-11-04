"""Reading a non-public image needs the right to read it; a public image is read with no credentials at all."""
import importlib
import io
import logging
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

OWNER = {"project_id": "p1", "role": "member", "sub": "u-owner"}
OTHER = {"project_id": "p2", "role": "member", "sub": "u-other"}
DOMAIN_ADMIN = {"project_id": "p9", "role": "member", "sub": "u-da", "domain_admin": True, "domain_id": "d1"}
DOMAIN_ADMIN_ELSEWHERE = {"project_id": "p8", "role": "member", "sub": "u-da2", "domain_admin": True, "domain_id": "d2"}
ADMIN = {"project_id": "admin", "role": "admin", "sub": "u-admin"}
INTERNAL = {"sub": "internal", "username": "admin", "project_id": "admin", "project_name": "admin", "role": "admin",
            "roles": ["admin"], "scope_type": "system", "jti": "internal"}  # what require_user_or_internal builds
ACCEPTED = {"project_id": "p3", "role": "member", "sub": "u-accepted"}
PENDING = {"project_id": "p4", "role": "member", "sub": "u-pending"}
REJECTED = {"project_id": "p5", "role": "member", "sub": "u-rejected"}
NO_PROJECT = {"role": "member", "sub": "s", "scope_type": "system"}
ANON = {"anon": True}  # the overrides raise 401 for it; OptionalUserOrInternal turns that into None

IDENTITIES = {
    "owner": OWNER, "other": OTHER, "domain_admin": DOMAIN_ADMIN, "domain_admin_elsewhere": DOMAIN_ADMIN_ELSEWHERE,
    "admin": ADMIN, "internal": INTERNAL, "accepted": ACCEPTED, "pending": PENDING, "rejected": REJECTED,
    "anon": ANON,
}
MAY_READ_PRIVATE = {"owner", "domain_admin", "admin", "internal", "accepted"}
NOT_FOUND = {"detail": "image 'victim' not found"}


@pytest.fixture
def img(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'i.sqlite'}")
    monkeypatch.setenv("IMAGE_STORE_PATH", str(tmp_path / "store"))
    import services.image.app.main as m

    m = importlib.reload(m)
    m.Base.metadata.create_all(bind=m.engine)
    from shared.auth_middleware import require_auth, require_user_or_internal

    who: dict = dict(OWNER)

    def identity():
        if who.get("anon"):
            raise HTTPException(status_code=401, detail="not authenticated")
        return who

    for dep in (require_auth, require_user_or_internal):
        m.app.dependency_overrides[dep] = identity
    monkeypatch.setattr("shared.domain.domain_projects", lambda d: ["p1", "p2"] if d == "d1" else [])
    m.client = TestClient(m.app)
    m.as_ = lambda identity_: (who.clear(), who.update(identity_))
    m.store = tmp_path / "store"
    return m


def make_image(m, name="victim", visibility="private", as_=OWNER):
    m.as_(as_)
    r = m.client.post("/images", data={"name": name, "disk_format": "raw", "visibility": visibility},
                      files={"file": ("f.img", io.BytesIO(b"hello"))})
    assert r.status_code == 201, r.text
    m.as_(OWNER)
    return r.json()


def add_members(m, image_id):
    with m.SessionLocal() as db:
        for project, status in (("p3", "accepted"), ("p4", "pending"), ("p5", "rejected")):
            db.add(m.ImageMember(image_id=image_id, member_id=project, status=status))
        db.commit()


def edit(m, ref, **fields):
    with m.SessionLocal() as db:
        image = m.get_image(db, ref)
        for k, v in fields.items():
            setattr(image, k, v)
        db.commit()


# route → (method, path, check of the 200 body)
ROUTES = {
    "show": ("/images/victim", lambda r, image: r.json() == image),
    "download": ("/images/victim/download", lambda r, image: r.content == b"hello"),
    "tags": ("/images/victim/tags", lambda r, image: r.json() == {"tags": ["t1"]}),
    "verify": ("/images/victim/verify", lambda r, image: r.json()["id"] == image["id"]),
    "members": ("/images/victim/members", lambda r, image: len(r.json()["members"]) == 3),
}
SIGNED_IN_ONLY = {"verify", "members"}


@pytest.mark.parametrize("visibility", ["public", "private"])
@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("who", IDENTITIES)
def test_the_matrix_of_identities_routes_and_visibility(img, who, route, visibility):
    image = make_image(img, visibility=visibility)
    add_members(img, image["id"])
    img.client.put("/images/victim/tags/t1")
    img.as_(IDENTITIES[who])
    path, check = ROUTES[route]

    r = img.client.get(path)

    if who == "anon" and route in SIGNED_IN_ONLY:
        assert r.status_code == 401, r.text
    elif visibility == "public" or who in MAY_READ_PRIVATE:
        assert r.status_code == 200, r.text
        assert check(r, image), r.text
    else:
        assert r.status_code == 404, r.text
        assert r.json() == NOT_FOUND


def test_a_refusal_has_the_body_of_a_missing_image(img):
    image = make_image(img)
    img.as_(OTHER)
    refused = img.client.get("/images/victim")
    img.as_(OWNER)
    img.client.delete("/images/victim")
    img.as_(OTHER)
    missing = img.client.get("/images/victim")
    assert (refused.status_code, refused.json()) == (missing.status_code, missing.json()) == (404, NOT_FOUND)
    assert image["id"] not in refused.text


@pytest.mark.parametrize("who, ok_private", [(OWNER, True), (OTHER, False), (ACCEPTED, True), (PENDING, False)],
                         ids=["owner", "other", "accepted", "pending"])
def test_prefetch_follows_the_rule(img, who, ok_private):
    image = make_image(img)
    add_members(img, image["id"])
    img.as_(who)
    r = img.client.post("/images/victim/prefetch")
    if ok_private:
        assert r.status_code == 200 and r.json()["image_id"] == image["id"], r.text
    else:
        assert (r.status_code, r.json()) == (404, NOT_FOUND)


def test_prefetch_of_a_public_image_is_open_to_any_signed_in_caller(img):
    make_image(img, visibility="public")
    img.as_(OTHER)
    assert img.client.post("/images/victim/prefetch").status_code == 200
    img.as_(ANON)
    assert img.client.post("/images/victim/prefetch").status_code == 401  # still signed-in-only


@pytest.mark.parametrize("visibility", ["public", "private"])
def test_an_image_without_an_owner(img, visibility):
    make_image(img, visibility=visibility)
    edit(img, "victim", project_id=None)
    for who in (ADMIN, INTERNAL):
        img.as_(who)
        assert img.client.get("/images/victim").status_code == 200
        assert img.client.get("/images/victim/download").content == b"hello"
    for who in (OWNER, OTHER, DOMAIN_ADMIN, ANON):
        img.as_(who)
        r = img.client.get("/images/victim")
        if visibility == "public":
            assert r.status_code == 200, r.text
        else:
            assert (r.status_code, r.json()) == (404, NOT_FOUND)


@pytest.mark.parametrize("visibility, status", [("private", 404), ("public", 200)])
def test_a_token_without_a_project_does_not_crash(img, visibility, status):
    make_image(img, visibility=visibility)
    img.as_(NO_PROJECT)
    for path in ("/images/victim", "/images/victim/download", "/images/victim/tags"):
        assert img.client.get(path).status_code == status, path
    assert img.client.get("/images/victim/verify").status_code == status


def test_a_public_image_downloads_with_no_credentials_at_all(img):
    make_image(img, visibility="public")
    img.as_(ANON)
    r = img.client.get("/images/victim/download")
    assert r.status_code == 200 and r.content == b"hello"


def test_a_public_image_downloads_through_the_real_auth_chain_with_no_header(img, monkeypatch):
    """No dependency override at all: no header, an unknown internal token, and a garbage bearer token."""
    make_image(img, visibility="public")
    private = make_image(img, name="hidden", visibility="private")
    monkeypatch.setenv("INTERNAL_TOKEN", "s3cret")
    img.app.dependency_overrides.clear()
    for headers in ({}, {"X-Internal-Token": "wrong"}, {"Authorization": "Bearer not-a-jwt"}):
        r = img.client.get("/images/victim/download", headers=headers)
        assert r.status_code == 200 and r.content == b"hello", headers
        assert img.client.get("/images/victim", headers=headers).status_code == 200
        hidden = img.client.get("/images/hidden", headers=headers)
        assert (hidden.status_code, hidden.json()) == (404, {"detail": "image 'hidden' not found"}), headers
        assert private["id"] not in hidden.text
    ok = img.client.get("/images/hidden/download", headers={"X-Internal-Token": "s3cret"})
    assert ok.status_code == 200 and ok.content == b"hello"


def test_a_public_minio_image_streams_with_no_credentials(img, monkeypatch):
    make_image(img, visibility="public")
    edit(img, "victim", storage_backend="minio", object_name="obj", size=5)

    class Body:
        def iter_chunks(self, chunk_size):
            yield b"hel"
            yield b"lo"

        def close(self):
            pass

    class FakeMinio:
        bucket = "b"
        client = SimpleNamespace(get_object=lambda Bucket, Key: {"Body": Body()})

    monkeypatch.setattr(img, "MinioStorageBackend", FakeMinio)
    img.as_(ANON)
    r = img.client.get("/images/victim/download")
    assert r.status_code == 200 and r.content == b"hello"
    edit(img, "victim", visibility="private")
    assert img.client.get("/images/victim/download").status_code == 404


def test_anonymous_refusal_logs_one_warning_and_a_signed_in_refusal_logs_none(img, caplog):
    make_image(img)
    caplog.set_level(logging.WARNING, logger="image-api")

    img.as_(ANON)
    assert img.client.get("/images/victim").status_code == 404
    warnings = [r for r in caplog.records if r.name == "image-api" and r.levelno == logging.WARNING]
    assert [r.getMessage() for r in warnings] == ["anonymous read of a non-public image refused: victim"]

    caplog.clear()
    img.as_(OTHER)
    assert img.client.get("/images/victim").status_code == 404
    assert [r for r in caplog.records if r.name == "image-api"] == []

    caplog.clear()
    img.as_(ANON)
    assert img.client.get("/images/nothing-here").status_code == 404  # a missing image is not a refusal
    assert [r for r in caplog.records if r.name == "image-api"] == []


def test_name_lookup_prefers_the_callers_own_image(img):
    # `name` is unique in the real schema, so a second image of the same name is put in the table directly
    with img.engine.begin() as c:
        c.exec_driver_sql("DROP INDEX ix_image_images_name")
        c.exec_driver_sql("CREATE INDEX ix_image_images_name ON image_images (name)")
    first = make_image(img, name="base", as_=OWNER)
    now = datetime.utcnow()
    with img.SessionLocal() as db:
        second = img.Image(id="second-id", name="base", filename="x", object_name="x", disk_format="raw",
                           container_format="bare", size=0, checksum="", status="ACTIVE", visibility="private",
                           project_id="p2", created_at=now + timedelta(seconds=5))
        db.add(second)
        db.commit()
    mine, theirs = first["id"], "second-id"

    def get(who):
        img.as_(who)
        r = img.client.get("/images/base")
        return r.json()["id"] if r.status_code == 200 else r.status_code

    # both private
    assert get(OWNER) == mine and get(OTHER) == theirs
    assert get(ACCEPTED) == 404 and get(ANON) == 404
    assert get(ADMIN) == mine  # nobody's own: the earliest
    # p2's is public: a stranger gets it, p1 still gets its own, p2 its own
    edit(img, mine, visibility="private")
    with img.SessionLocal() as db:
        db.get(img.Image, theirs).visibility = "public"
        db.commit()
    assert get(ACCEPTED) == theirs and get(ANON) == theirs
    assert get(OWNER) == mine and get(OTHER) == theirs
    # p1's is public, p2's private: p2 gets its own before the public one
    edit(img, mine, visibility="public")
    with img.SessionLocal() as db:
        db.get(img.Image, theirs).visibility = "private"
        db.commit()
    assert get(OTHER) == theirs and get(OWNER) == mine and get(ACCEPTED) == mine
    # both public, neither the caller's: the earliest
    with img.SessionLocal() as db:
        db.get(img.Image, theirs).visibility = "public"
        db.commit()
    assert get(ACCEPTED) == mine and get(ANON) == mine
    # the same lookup behind the other routes
    img.as_(OWNER)
    assert img.client.get("/images/base/download").content == b"hello"  # p1's own, the one with a file
    img.as_(OTHER)
    assert img.client.get("/images/base/tags").status_code == 200


def test_lookup_by_id_applies_the_rule(img):
    private = make_image(img, name="p-img", visibility="private")
    public = make_image(img, name="pub-img", visibility="public")
    for who, private_status in ((OWNER, 200), (OTHER, 404), (ANON, 404)):
        img.as_(who)
        r = img.client.get(f"/images/{private['id']}")
        assert r.status_code == private_status, (who, r.text)
        if private_status == 404:
            assert r.json() == {"detail": f"image '{private['id']}' not found"}
        assert img.client.get(f"/images/{public['id']}").status_code == 200
        assert img.client.get(f"/images/{public['id']}/download").content == b"hello"
