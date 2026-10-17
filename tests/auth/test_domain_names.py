"""Names are unique per domain: the same project name and username can live in two domains."""
import pytest
from fastapi.testclient import TestClient

from tests.stack import fresh_service


@pytest.fixture
def auth(monkeypatch, tmp_path):
    a = fresh_service("auth", monkeypatch, tmp_path)
    m = a.models
    with a.db.SessionLocal() as db:
        db.add_all([m.Domain(id="default", name="Default"), m.Domain(id="d1", name="one")])
        db.flush()
        db.add_all([m.Project(id="pd", name="web", domain_id="default"), m.Project(id="p1", name="web", domain_id="d1")])
        db.flush()
        for uid, pw, pid, dom, role in (("sys", "a", "pd", "default", "admin"), ("ud", "pd-pw", "pd", "default", "member"),
                                        ("u1", "p1-pw", "p1", "d1", "member"), ("da1", "a", "p1", "d1", "member")):
            name = "bob" if uid in ("ud", "u1") else uid
            db.add(m.User(id=uid, username=name, email=f"{uid}@x", password_hash=a.tokens._hash_password(pw),
                          project_id=pid, domain_id=dom, role=role, is_domain_admin=uid == "da1"))
        db.commit()
    a.client = TestClient(a.app)
    return a


def login(auth, **kw):
    return auth.client.post("/auth/tokens", json=kw)


def test_same_username_logs_in_by_password_or_domain(auth):
    assert auth.tokens._decode_token(login(auth, username="bob", password="p1-pw").json()["token"])["domain_id"] == "d1"
    assert auth.tokens._decode_token(login(auth, username="bob", password="pd-pw", domain="Default").json()["token"])["domain_id"] == "default"
    assert login(auth, username="bob", password="pd-pw", domain="d1").status_code == 401
    assert login(auth, username="bob", password="nope").status_code == 401


def test_lookup_by_name_is_per_domain(auth):
    tok = auth.client.post("/auth/tokens", json={"username": "da1", "password": "a"}).json()["token"]
    h = {"X-Auth-Token": tok}
    assert auth.client.get("/projects/web", headers=h).json()["id"] == "p1"
    sys = auth.client.post("/auth/tokens", json={"username": "sys", "password": "a"}).json()["token"]
    r = auth.client.get("/projects/web", headers={"X-Auth-Token": sys})
    assert r.status_code == 409 and "ambiguous" in r.json()["detail"]
    assert auth.client.get("/projects/pd", headers={"X-Auth-Token": sys}).status_code == 200


def test_duplicates_refused_inside_one_domain_only(auth):
    tok = auth.client.post("/auth/tokens", json={"username": "da1", "password": "a"}).json()["token"]
    h = {"X-Auth-Token": tok}
    assert auth.client.post("/projects", json={"name": "web"}, headers=h).status_code == 409
    assert auth.client.post("/projects", json={"name": "api"}, headers=h).status_code == 201
    new = {"username": "bob", "email": "n@x", "password": "pw12345678", "project_id": "p1"}
    assert auth.client.post("/users", json=new, headers=h).status_code == 409
    sys = {"X-Auth-Token": auth.client.post("/auth/tokens", json={"username": "sys", "password": "a"}).json()["token"]}
    assert auth.client.post("/users", json={**new, "username": "bob2", "project_id": "pd"}, headers=sys).status_code == 201
