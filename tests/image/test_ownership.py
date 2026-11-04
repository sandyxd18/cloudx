"""Changing an image needs its owner (or an admin); system-managed fields need an admin; the file store stays inside its folder."""
import importlib
import io

import pytest
from fastapi.testclient import TestClient

OWNER = {"project_id": "p1", "role": "member", "sub": "u-owner"}
OTHER = {"project_id": "p2", "role": "member", "sub": "u-other"}
ADMIN = {"project_id": "admin", "role": "admin", "sub": "u-admin"}
DOMAIN_ADMIN = {"project_id": "p9", "role": "member", "sub": "u-da", "domain_admin": True, "domain_id": "d1"}
INTERNAL = {"sub": "internal", "username": "admin", "project_id": "admin", "project_name": "admin", "role": "admin",
            "roles": ["admin"], "scope_type": "system", "jti": "internal"}  # what require_user_or_internal builds


@pytest.fixture
def img(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'i.sqlite'}")
    monkeypatch.setenv("IMAGE_STORE_PATH", str(tmp_path / "store"))
    import services.image.app.main as m

    m = importlib.reload(m)
    m.Base.metadata.create_all(bind=m.engine)
    from shared.auth_middleware import require_auth, require_user_or_internal

    who: dict = dict(OWNER)
    for dep in (require_auth, require_user_or_internal):
        m.app.dependency_overrides[dep] = lambda: who
    monkeypatch.setattr("shared.domain.domain_projects", lambda d: ["p1", "p2"] if d == "d1" else [])
    m.client = TestClient(m.app)
    m.as_ = lambda identity: (who.clear(), who.update(identity))
    m.store = tmp_path / "store"
    m.tmp = tmp_path
    return m


def make_image(m, name="victim", as_=OWNER):
    m.as_(as_)
    r = m.client.post("/images", data={"name": name, "disk_format": "raw"}, files={"file": ("f.img", io.BytesIO(b"hello"))})
    assert r.status_code == 201, r.text
    m.as_(OWNER)
    return r.json()


def row(m, ref="victim"):
    with m.SessionLocal() as db:
        return m.get_image(db, ref)


# (method, path, json body, success status) for every mutation the owner may make
MUTATIONS = [
    ("patch", "/images/victim", {"name": "renamed"}, 200),
    ("delete", "/images/victim", None, 200),
    ("post", "/images/victim/deactivate", None, 200),
    ("post", "/images/victim/reactivate", None, 200),
    ("put", "/images/victim/tags/t1", None, 200),
    ("delete", "/images/victim/tags/t1", None, 204),
    ("post", "/images/victim/members", {"member": "p2"}, 201),
    ("delete", "/images/victim/members/p2", None, 204),
]
IDS = [f"{m} {p}" for m, p, _, _ in MUTATIONS]


@pytest.mark.parametrize("method, path, body, ok", MUTATIONS, ids=IDS)
def test_a_member_of_another_project_is_refused(img, method, path, body, ok):
    make_image(img)
    img.client.post("/images/victim/members", json={"member": "p2"})  # a member row to try to remove
    img.client.put("/images/victim/tags/t1")                          # a tag to try to remove
    img.as_(OTHER)

    r = img.client.request(method, path, **({"json": body} if body is not None else {}))

    assert r.status_code == 403, r.text
    assert r.json()["detail"] == "not authorized to modify this image"
    image = row(img)
    assert image is not None and image.name == "victim" and image.status == "ACTIVE"
    assert img.client.get("/images/victim/members").status_code in (200, 403)  # still there to look at


@pytest.mark.parametrize("who", [OWNER, ADMIN, INTERNAL], ids=["owner", "admin", "internal"])
@pytest.mark.parametrize("method, path, body, ok", MUTATIONS, ids=IDS)
def test_owner_admin_and_internal_may_change_an_image(img, who, method, path, body, ok):
    make_image(img)
    img.client.post("/images/victim/members", json={"member": "p2"})
    img.client.put("/images/victim/tags/t1")
    img.as_(who)

    r = img.client.request(method, path, **({"json": body} if body is not None else {}))

    assert r.status_code == ok, r.text


def test_an_admin_of_the_images_domain_may_change_it_and_one_of_another_domain_may_not(img, monkeypatch):
    make_image(img)
    img.as_(DOMAIN_ADMIN)
    assert img.client.post("/images/victim/deactivate").status_code == 200
    monkeypatch.setattr("shared.domain.domain_projects", lambda d: ["other-project"])
    assert img.client.post("/images/victim/reactivate").status_code == 403


def test_an_image_without_an_owner_is_for_admins_only(img):
    make_image(img)
    with img.SessionLocal() as db:
        image = img.get_image(db, "victim")
        image.project_id = None
        db.commit()
    for who in (OWNER, OTHER):
        img.as_(who)
        assert img.client.delete("/images/victim").status_code == 403
    img.as_(ADMIN)
    assert img.client.delete("/images/victim").status_code == 200


# ── fields ─────────────────────────────────────────────────────────────────────

SYSTEM_FIELDS = {"status": "deleted", "size": 1, "checksum": "x", "object_name": "other", "filename": "other",
                 "storage_backend": "minio"}


@pytest.mark.parametrize("field", sorted(SYSTEM_FIELDS))
def test_the_owner_cannot_change_a_system_managed_field(img, field):
    make_image(img)
    before = {k: getattr(row(img), k) for k in SYSTEM_FIELDS}

    r = img.client.patch("/images/victim", json={field: SYSTEM_FIELDS[field], "name": "also-renamed"})

    assert r.status_code == 403, r.text
    assert field in r.json()["detail"]
    image = row(img)
    assert {k: getattr(image, k) for k in SYSTEM_FIELDS} == before and image.name == "victim"  # nothing was applied


def test_the_owner_still_changes_the_fields_meant_for_users(img):
    make_image(img)
    r = img.client.patch("/images/victim", json={
        "name": "n2", "visibility": "shared", "protected": True, "min_ram_mb": 256, "disk_format": "qcow2",
        "container_format": "bare", "properties": {"os_distro": "ubuntu"}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["name"], body["visibility"], body["protected"], body["min_ram_mb"]) == ("n2", "shared", True, 256)
    assert body["properties"] == {"os_distro": "ubuntu"}


@pytest.mark.parametrize("who", [ADMIN, INTERNAL], ids=["admin", "internal (image-worker)"])
def test_an_admin_or_the_internal_token_changes_system_fields(img, who):
    make_image(img)
    img.as_(who)
    r = img.client.patch("/images/victim", json={"status": "ACTIVE", "size": 7, "checksum": "abc", "storage_backend": "file"})
    assert r.status_code == 200, r.text
    assert (r.json()["size"], r.json()["checksum"]) == (7, "abc")


# ── the file store stays inside its folder ─────────────────────────────────────

def point_at(m, object_name):
    with m.SessionLocal() as db:
        image = m.get_image(db, "victim")
        image.object_name = object_name
        db.commit()


def test_download_refuses_a_name_that_leaves_the_store(img):
    outside = img.tmp / "outside.txt"
    outside.write_text("NOT-AN-IMAGE")
    make_image(img)
    for name in (str(outside), "../outside.txt", "sub/../../outside.txt"):
        point_at(img, name)
        r = img.client.get("/images/victim/download")
        assert r.status_code == 400, (name, r.status_code)
        assert r.json() == {"detail": "invalid image object name"}
        assert b"NOT-AN-IMAGE" not in r.content


def test_delete_refuses_a_name_that_leaves_the_store(img):
    outside = img.tmp / "outside.txt"
    outside.write_text("KEEP-ME")
    make_image(img)
    point_at(img, str(outside))

    r = img.client.delete("/images/victim")

    assert r.status_code == 400 and outside.read_text() == "KEEP-ME"
    assert row(img) is not None  # the record stays too


def test_a_symlink_inside_the_store_cannot_lead_out_of_it(img):
    outside = img.tmp / "outside.txt"
    outside.write_text("NOT-AN-IMAGE")
    make_image(img)
    (img.store / "link").symlink_to(outside)
    point_at(img, "link")

    assert img.client.get("/images/victim/download").status_code == 400


def test_a_normal_download_and_delete_still_work(img):
    make_image(img)
    assert img.client.get("/images/victim/download").content == b"hello"
    assert img.client.delete("/images/victim").status_code == 200
    assert not any(img.store.iterdir())
