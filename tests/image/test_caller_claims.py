"""image-api honours X-Caller-Claims from a holder of the internal token, through the real auth chain."""
import importlib
import io
import json

import pytest
from fastapi.testclient import TestClient

from shared.internal import on_behalf_headers

OWNER = {"project_id": "p1", "role": "member", "roles": ["member"], "sub": "u-owner", "username": "owner",
         "scope_type": "project"}
OTHER = {"project_id": "p2", "role": "member", "roles": ["member"], "sub": "u-other", "scope_type": "project"}
ACCEPTED = {"project_id": "p3", "role": "member", "roles": ["member"], "sub": "u-accepted", "scope_type": "project"}
PENDING = {"project_id": "p4", "role": "member", "roles": ["member"], "sub": "u-pending", "scope_type": "project"}
DOMAIN_ADMIN = {"project_id": "p9", "role": "member", "roles": ["member"], "sub": "u-da", "domain_admin": True,
                "domain_id": "d1", "scope_type": "project"}  # d1 holds the owner's project p1
DOMAIN_ADMIN_ELSEWHERE = {"project_id": "p8", "role": "member", "roles": ["member"], "sub": "u-da2",
                          "domain_admin": True, "domain_id": "d2", "scope_type": "project"}
ADMIN = {"project_id": "admin", "role": "admin", "roles": ["admin"], "sub": "u-admin", "scope_type": "project"}
NO_PROJECT = {"role": "member", "roles": ["member"], "sub": "s", "scope_type": "system"}
INTERNAL = {"sub": "internal", "username": "admin", "project_id": "admin", "role": "admin", "roles": ["admin"],
            "scope_type": "system", "jti": "internal"}

IDENTITIES = {"owner": OWNER, "other": OTHER, "accepted": ACCEPTED, "pending": PENDING, "domain_admin": DOMAIN_ADMIN,
              "domain_admin_elsewhere": DOMAIN_ADMIN_ELSEWHERE, "admin": ADMIN, "no_project": NO_PROJECT,
              "internal": INTERNAL}
MAY_READ_PRIVATE = {"owner", "accepted", "domain_admin", "admin", "internal"}
ROUTES = {"show": "/images/victim", "download": "/images/victim/download", "tags": "/images/victim/tags"}
NOT_FOUND = {"detail": "image 'victim' not found"}
TOKEN = "s3cret"


@pytest.fixture
def img(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'i.sqlite'}")
    monkeypatch.setenv("IMAGE_STORE_PATH", str(tmp_path / "store"))
    monkeypatch.setenv("INTERNAL_TOKEN", TOKEN)
    monkeypatch.setenv("REVOCATION_CHECK", "false")
    import services.image.app.main as m

    m = importlib.reload(m)
    m.Base.metadata.create_all(bind=m.engine)
    from shared.auth_middleware import require_auth, require_user_or_internal

    monkeypatch.setattr("shared.domain.domain_projects", lambda d: ["p1", "p2"] if d == "d1" else [])
    m.client = TestClient(m.app)

    def make(name="victim", visibility="private"):
        for dep in (require_auth, require_user_or_internal):
            m.app.dependency_overrides[dep] = lambda: OWNER  # only to create the image; cleared below
        r = m.client.post("/images", data={"name": name, "disk_format": "raw", "visibility": visibility},
                          files={"file": ("f.img", io.BytesIO(b"hello"))})
        assert r.status_code == 201, r.text
        m.app.dependency_overrides.clear()  # from here on, the real auth chain
        with m.SessionLocal() as db:
            for project, status in (("p3", "accepted"), ("p4", "pending")):
                db.add(m.ImageMember(image_id=r.json()["id"], member_id=project, status=status))
            db.commit()
        return r.json()

    m.make = make
    return m


@pytest.mark.parametrize("visibility", ["public", "private"])
@pytest.mark.parametrize("who", IDENTITIES)
@pytest.mark.parametrize("route", ROUTES)
def test_the_rule_applies_to_the_stated_identity(img, who, route, visibility):
    img.make(visibility=visibility)

    r = img.client.get(ROUTES[route], headers=on_behalf_headers(IDENTITIES[who]))

    if visibility == "public" or who in MAY_READ_PRIVATE:
        assert r.status_code == 200, r.text
    else:
        assert r.status_code == 404, r.text
        assert r.json() == NOT_FOUND


def test_the_content_comes_back_for_a_permitted_claims_caller(img):
    image = img.make()
    h = on_behalf_headers(ACCEPTED)
    assert img.client.get("/images/victim", headers=h).json() == image
    assert img.client.get("/images/victim/download", headers=h).content == b"hello"


def test_the_internal_token_alone_reads_the_private_image(img):
    img.make()
    h = {"X-Internal-Token": TOKEN}
    for path in ROUTES.values():
        assert img.client.get(path, headers=h).status_code == 200, path


def test_on_behalf_of_no_current_or_the_internal_caller_reads_the_private_image(img):
    img.make()
    for current in (None, INTERNAL):
        assert img.client.get("/images/victim", headers=on_behalf_headers(current)).status_code == 200


def test_a_claims_header_with_a_wrong_or_missing_token_reads_as_anonymous(img):
    img.make()
    claims = on_behalf_headers(OWNER)["X-Caller-Claims"]  # the owner's own claims
    for headers in ({"X-Caller-Claims": claims}, {"X-Internal-Token": "wrong", "X-Caller-Claims": claims},
                    {"X-Internal-Token": "", "X-Caller-Claims": claims}):
        for path in ROUTES.values():
            r = img.client.get(path, headers=headers)
            assert (r.status_code, r.json()) == (404, NOT_FOUND), (headers, path)


def test_a_claims_header_with_the_token_unset_is_ignored(img, monkeypatch):
    img.make()
    h = on_behalf_headers(OWNER)
    monkeypatch.delenv("INTERNAL_TOKEN")
    assert img.client.get("/images/victim", headers=h).status_code == 404
    assert img.client.get("/images/victim", headers={**h, "X-Internal-Token": ""}).status_code == 404


def test_an_invalid_header_with_a_valid_token_is_anonymous_not_admin(img):
    img.make("hidden")
    public = img.make("open", visibility="public")
    bad = ["{nope", "[]", json.dumps({"project_id": "p1"}), json.dumps({"role": "member", "domain_admin": "true"}),
           json.dumps({"role": "admin", "domain_admin": 1}), "x" * 3000,
           json.dumps({"role": "admin", "roles": [1]})]
    for value in bad:
        h = {"X-Internal-Token": TOKEN, "X-Caller-Claims": value}
        for path in ("/images/hidden", "/images/hidden/download", "/images/hidden/tags"):
            r = img.client.get(path, headers=h)
            assert (r.status_code, r.json()) == (404, {"detail": "image 'hidden' not found"}), (value[:30], path)
        assert img.client.get("/images/open", headers=h).json() == public  # public stays readable


def test_claims_do_not_reach_the_other_internal_aware_paths(img):
    """A route with CurrentUser (verify, members) takes no internal token, claims or not."""
    img.make()
    r = img.client.get("/images/victim/members", headers=on_behalf_headers(OWNER))
    assert r.status_code == 401, r.text


def test_the_real_jwt_still_works_next_to_the_claims_path(img):
    from datetime import datetime, timedelta, timezone

    from jose import jwt

    from shared import auth_middleware as am

    img.make()
    claims = {**OWNER, "exp": datetime.now(timezone.utc) + timedelta(hours=1)}
    token = jwt.encode(claims, am.SECRET_KEY, algorithm=am.ALGORITHM)
    assert img.client.get("/images/victim", headers={"X-Auth-Token": token}).status_code == 200
    other = jwt.encode({**OTHER, "exp": claims["exp"]}, am.SECRET_KEY, algorithm=am.ALGORITHM)
    assert img.client.get("/images/victim", headers={"X-Auth-Token": other}).status_code == 404
