"""Domain and system scoped tokens (POST /auth/tokens with a `scope` object)."""
import pytest
from fastapi.testclient import TestClient

from tests.stack import fresh_service


@pytest.fixture
def auth(monkeypatch, tmp_path):
    a = fresh_service("auth", monkeypatch, tmp_path)
    pw = a.tokens._hash_password("pw")
    with a.db.SessionLocal() as db:
        m = a.models
        db.add_all([m.Domain(id="default", name="Default"), m.Domain(id="d1", name="one"),
                    m.Domain(id="d2", name="two"), m.Domain(id="d3", name="three", enabled=False)])
        db.flush()
        db.add_all([
            m.Project(id="pd", name="p-default", domain_id="default"),
            m.Project(id="p1", name="p-one", domain_id="d1"),
            m.Project(id="p2", name="p-two", domain_id="d2"),
        ])
        db.flush()
        db.add_all([
            m.User(id="sys", username="sys", email="s@x", password_hash=pw, project_id="pd", domain_id="default", role="admin"),
            m.User(id="da1", username="da1", email="a1@x", password_hash=pw, project_id="p1", domain_id="d1", is_domain_admin=True),
            m.User(id="da2", username="da2", email="a2@x", password_hash=pw, project_id="p2", domain_id="d2", is_domain_admin=True),
            m.User(id="u1", username="u1", email="u1@x", password_hash=pw, project_id="p1", domain_id="d1"),
            m.User(id="u1b", username="u1b", email="u1b@x", password_hash=pw, project_id="p1", domain_id="d1"),
        ])
        db.commit()
    a.client = TestClient(a.app)
    return a


def login(auth, username, **extra):
    return auth.client.post("/auth/tokens", json={"username": username, "password": "pw", **extra})


def claims(auth, r):
    return auth.tokens._decode_token(r.json()["token"])


def test_no_scope_is_project_scoped_as_before(auth):
    r = login(auth, "da1")
    assert r.status_code == 201
    c = claims(auth, r)
    assert (c["scope_type"], c["project_id"], c["project_name"], c["domain_id"], c["domain_admin"], c["role"]) == \
        ("project", "p1", "p-one", "d1", True, "member")
    assert r.json()["project"]["id"] == "p1" and "scope" not in r.json()


def test_scope_project_behaves_like_the_project_field(auth):
    r = login(auth, "sys", scope={"project": "p2"})
    assert r.status_code == 201 and claims(auth, r)["project_id"] == "p2"
    assert login(auth, "sys", project="p2", scope={"project": "p2"}).status_code == 201
    assert login(auth, "sys", project="p1", scope={"project": "p2"}).status_code == 400


def test_domain_scope_for_domain_admin(auth):
    for ref in ("d1", "one"):  # id or name
        r = login(auth, "da1", scope={"domain": ref})
        assert r.status_code == 201
        c = claims(auth, r)
        assert (c["scope_type"], c["project_id"], c["project_name"], c["domain_id"], c["domain_admin"]) == \
            ("domain", "", "", "d1", True)
        assert c["role"] == "member" and c["roles"] == ["member"]  # never escalated
        body = r.json()
        assert body["scope"] == {"type": "domain", "domain_id": "d1"} and "project" not in body
        assert body["token"] and body["expires_in"] and body["user"]["id"] == "da1" and "catalog" in body


def test_domain_scope_for_system_admin_can_name_any_domain(auth):
    r = login(auth, "sys", scope={"domain": "d2"})
    assert r.status_code == 201
    c = claims(auth, r)
    assert (c["scope_type"], c["domain_id"], c["domain_admin"], c["role"], c["project_id"]) == ("domain", "d2", True, "admin", "")


def test_domain_scope_for_other_domain_is_403(auth):
    assert login(auth, "da1", scope={"domain": "d2"}).status_code == 403
    assert login(auth, "da1", scope={"domain": "two"}).status_code == 403


def test_domain_scope_for_plain_member_is_403(auth):
    assert login(auth, "u1", scope={"domain": "d1"}).status_code == 403


def test_domain_scope_unknown_or_disabled_domain(auth):
    assert login(auth, "sys", scope={"domain": "nope"}).status_code == 404
    r = login(auth, "sys", scope={"domain": "d3"})
    assert r.status_code == 403 and "disabled" in r.json()["detail"]


def test_system_scope_for_admin(auth):
    r = login(auth, "sys", scope={"system": True})
    assert r.status_code == 201
    c = claims(auth, r)
    assert (c["scope_type"], c["role"], c["roles"], c["project_id"], c["project_name"]) == \
        ("system", "admin", ["admin"], "pd", "p-default")
    assert r.json()["scope"]["type"] == "system"


def test_system_scope_for_non_admin_is_403(auth):
    assert login(auth, "da1", scope={"system": True}).status_code == 403
    assert login(auth, "u1", scope={"system": True}).status_code == 403


def test_two_scope_fields_is_400(auth):
    assert login(auth, "sys", scope={"project": "p1", "domain": "d1"}).status_code == 400
    assert login(auth, "sys", scope={"domain": "d1", "system": True}).status_code == 400
    assert login(auth, "sys", scope={}).status_code == 400
    assert login(auth, "sys", scope={"system": False}).status_code == 400


def test_create_token_signature_defaults_and_overrides(auth):
    with auth.db.SessionLocal() as db:
        u = db.get(auth.models.User, "da1")
        c = auth.tokens._decode_token(auth.tokens._create_token(u))
        assert (c["project_id"], c["domain_id"], c["domain_admin"]) == ("p1", "d1", True)
        c = auth.tokens._decode_token(auth.tokens._create_token(u, project_id="", domain_id="dX"))
        assert (c["project_id"], c["domain_id"]) == ("", "dX")
        u2 = db.get(auth.models.User, "u1")
        c = auth.tokens._decode_token(auth.tokens._create_token(u2, scope_type="domain", project_id=""))
        assert c["domain_admin"] is True  # forced for domain scope
        assert auth.tokens._decode_token(auth.tokens._create_token(u2))["domain_admin"] is False


def test_domain_token_can_list_users_of_its_domain_in_auth_api(auth):
    tok = login(auth, "da1", scope={"domain": "d1"}).json()["token"]
    r = auth.client.get("/users", headers={"X-Auth-Token": tok})
    assert r.status_code == 200
    assert {u["id"] for u in r.json()["items"]} == {"da1", "u1", "u1b"}


def test_domain_token_cannot_create_project_bound_rows(auth):
    h = {"X-Auth-Token": login(auth, "da1", scope={"domain": "d1"}).json()["token"]}
    assert auth.client.post("/groups", json={"name": "g"}, headers=h).status_code == 403
    assert auth.client.post("/access-keys", json={"name": "k"}, headers=h).status_code == 403


def test_user_or_internal_dependency_takes_a_non_ascii_internal_token_as_no_token(auth, monkeypatch):
    monkeypatch.setenv("INTERNAL_TOKEN", "tok")
    cafe = "café".encode("latin-1")
    user = {"Authorization": f"Bearer {login(auth, 'u1').json()['token']}"}
    assert auth.client.get("/projects/p1/quota", headers={**user, "X-Internal-Token": cafe}).status_code == 200
    assert auth.client.get("/projects/p1/quota", headers={"X-Internal-Token": cafe}).status_code == 401
    assert auth.client.get("/projects/p1/quota", headers={**user, "X-Internal-Token": "tok"}).status_code == 200
