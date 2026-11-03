"""A service that uploads an image for a user (block-store-worker, after a server snapshot) acts as admin of the
"admin" project, so the image would belong to a project the user cannot see. It names the project in the form; a user
cannot do that for themselves."""
import io

from tests.image import test_glance_fields

img = test_glance_fields.img


def _upload(m, **form):
    return m.client.post("/images", data={"name": "snap1", "disk_format": "raw", **form}, files={"file": ("f.img", io.BytesIO(b"x"))})


def _owner(m):
    with m.SessionLocal() as db:
        return db.query(m.Image).filter(m.Image.name == "snap1").one().project_id


def _as(m, caller):
    from shared.auth_middleware import require_auth, require_user_or_internal

    for dep in (require_auth, require_user_or_internal):
        m.app.dependency_overrides[dep] = lambda: caller


def test_an_internal_upload_can_name_the_owning_project(img):
    _as(img, {"sub": "internal", "project_id": "admin", "role": "admin"})

    assert _upload(img, project_id="p7", visibility="private").status_code == 201

    assert _owner(img) == "p7"


def test_an_internal_upload_without_a_project_keeps_the_callers(img):
    _as(img, {"sub": "internal", "project_id": "admin", "role": "admin"})

    assert _upload(img).status_code == 201

    assert _owner(img) == "admin"


def test_a_user_cannot_upload_into_another_project(img):
    _as(img, {"sub": "u1", "project_id": "p1", "role": "admin"})

    assert _upload(img, project_id="p7").status_code == 201

    assert _owner(img) == "p1"
