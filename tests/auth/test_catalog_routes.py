"""GET /v3/auth/catalog (any logged-in user) and GET /internal/catalog (services)."""
import pytest
from fastapi.testclient import TestClient

from tests.stack import fresh_service


@pytest.fixture
def auth(monkeypatch, tmp_path):
    monkeypatch.setenv("REVOCATION_CHECK", "false")
    a = fresh_service("auth", monkeypatch, tmp_path)
    m = a.models
    with a.db.SessionLocal() as db:
        db.add(m.Domain(id="default", name="Default"))
        db.flush()
        db.add(m.Project(id="p1", name="demo", domain_id="default"))
        db.flush()
        for name, role in (("root", "admin"), ("bob", "member")):
            db.add(m.User(id=name, username=name, email=f"{name}@x", password_hash=a.tokens._hash_password("pw"),
                          project_id="p1", domain_id="default", role=role))
        db.commit()
        a.bootstrap._bootstrap_service_catalog(db)
    a.client = TestClient(a.app)
    return a


def headers(auth, username):
    r = auth.client.post("/auth/tokens", json={"username": username, "password": "pw"})
    return {"X-Auth-Token": r.json()["token"]}


def image_endpoints(body):
    entry = next(s for s in body["catalog"] if s["name"] == "image")
    return {e["interface"]: e["url"] for e in entry["endpoints"]}


def test_user_catalog_lists_image_with_an_internal_endpoint(auth):
    r = auth.client.get("/v3/auth/catalog", headers=headers(auth, "bob"))
    assert r.status_code == 200
    assert "internal" in image_endpoints(r.json())


def test_user_catalog_needs_a_token(auth):
    r = auth.client.get("/v3/auth/catalog")
    assert r.status_code == 401 and "catalog" not in r.json()


def test_internal_catalog_matches_the_login_catalog(auth):
    login = auth.client.post("/auth/tokens", json={"username": "bob", "password": "pw"}).json()
    r = auth.client.get("/internal/catalog", headers=headers(auth, "root"))
    assert r.status_code == 200
    assert r.json() == {"catalog": login["catalog"]}
    assert "internal" in image_endpoints(r.json())


def test_internal_catalog_skips_disabled_endpoints(auth):
    m = auth.models
    with auth.db.SessionLocal() as db:
        db.execute(m.ServiceEndpoint.__table__.update().where(m.ServiceEndpoint.interface == "internal").values(enabled=False))
        db.commit()
    r = auth.client.get("/internal/catalog", headers=headers(auth, "root"))
    assert "internal" not in image_endpoints(r.json())


def test_internal_catalog_with_the_internal_token(auth, monkeypatch):
    monkeypatch.setenv("INTERNAL_TOKEN", "s3cret-internal")
    r = auth.client.get("/internal/catalog", headers={"X-Internal-Token": "s3cret-internal"})
    assert r.status_code == 200
    assert "internal" in image_endpoints(r.json())


@pytest.mark.parametrize("extra", [{}, {"X-Internal-Token": "wrong"}])
def test_internal_catalog_rejects_a_missing_or_wrong_token(auth, monkeypatch, extra):
    monkeypatch.setenv("INTERNAL_TOKEN", "s3cret-internal")
    r = auth.client.get("/internal/catalog", headers=extra)
    assert r.status_code == 401 and "catalog" not in r.json()


def test_internal_catalog_accepts_an_admin_jwt_without_the_token(auth, monkeypatch):
    monkeypatch.setenv("INTERNAL_TOKEN", "s3cret-internal")
    r = auth.client.get("/internal/catalog", headers=headers(auth, "root"))
    assert r.status_code == 200
    assert "internal" in image_endpoints(r.json())


def test_internal_catalog_rejects_a_non_admin_jwt(auth, monkeypatch):
    monkeypatch.setenv("INTERNAL_TOKEN", "s3cret-internal")
    r = auth.client.get("/internal/catalog", headers=headers(auth, "bob"))
    assert r.status_code == 403 and "catalog" not in r.json()
