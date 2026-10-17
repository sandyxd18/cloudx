"""Domain isolation: projects, users, groups and role assignments belong to one domain; a domain admin stays inside it."""
import pytest
from fastapi.testclient import TestClient

from tests.stack import fresh_service

@pytest.fixture
def auth(monkeypatch, tmp_path):
    a = fresh_service("auth", monkeypatch, tmp_path)
    pw = a.tokens._hash_password("pw")
    with a.db.SessionLocal() as db:
        m = a.models
        db.add_all([m.Domain(id="default", name="Default"), m.Domain(id="d1", name="one"), m.Domain(id="d2", name="two")])
        db.flush()
        db.add_all([
            m.Project(id="pd", name="p-default", domain_id="default"),
            m.Project(id="p1", name="p-one", domain_id="d1"),
            m.Project(id="p1b", name="p-one-b", domain_id="d1"),
            m.Project(id="p2", name="p-two", domain_id="d2"),
        ])
        db.flush()
        db.add_all([
            m.User(id="sys", username="sys", email="s@x", password_hash=pw, project_id="pd", domain_id="default", role="admin"),
            m.User(id="da1", username="da1", email="a1@x", password_hash=pw, project_id="p1", domain_id="d1", is_domain_admin=True),
            m.User(id="da2", username="da2", email="a2@x", password_hash=pw, project_id="p2", domain_id="d2", is_domain_admin=True),
            m.User(id="u1", username="u1", email="u1@x", password_hash=pw, project_id="p1", domain_id="d1"),
            m.User(id="u2", username="u2", email="u2@x", password_hash=pw, project_id="p2", domain_id="d2"),
        ])
        db.commit()
    a.client = TestClient(a.app)
    return a

def login(auth, username, project=None):
    body = {"username": username, "password": "pw", **({"project": project} if project else {})}
    return auth.client.post("/auth/tokens", json=body)

def hdr(auth, username):
    return {"X-Auth-Token": login(auth, username).json()["token"]}

def test_token_carries_domain_claims(auth):
    claims = auth.tokens._decode_token(login(auth, "da1").json()["token"])
    assert claims["domain_id"] == "d1" and claims["domain_admin"] is True and claims["role"] == "member"
    assert auth.tokens._decode_token(login(auth, "u1").json()["token"])["domain_admin"] is False

def test_user_and_project_default_to_default_domain(auth):
    with auth.db.SessionLocal() as db:
        m = auth.models
        db.add(m.Project(id="px", name="px"))
        db.flush()
        db.add(m.User(id="ux", username="ux", email="ux@x", password_hash="h", project_id="px"))
        db.commit()
        assert db.get(m.Project, "px").domain_id == "default" and db.get(m.User, "ux").domain_id == "default"
        assert db.get(m.User, "ux").is_domain_admin is False


# ---- projects, users, domains ----------------------------------------------------------------

def test_domain_admin_lists_only_own_projects(auth):
    ids = {p["id"] for p in auth.client.get("/projects", headers=hdr(auth, "da1")).json()["items"]}
    assert ids == {"p1", "p1b"}
    sys_ids = {p["id"] for p in auth.client.get("/projects", headers=hdr(auth, "sys")).json()["items"]}
    assert {"pd", "p1", "p2"} <= sys_ids

def test_domain_admin_cannot_show_update_delete_foreign_project_by_id(auth):
    h = hdr(auth, "da1")
    assert auth.client.get("/projects/p2", headers=h).status_code == 404
    assert auth.client.put("/projects/p2", json={"description": "x"}, headers=h).status_code == 404
    assert auth.client.delete("/projects/p2", headers=h).status_code == 404

def test_domain_admin_create_project_forced_into_own_domain(auth):
    h = hdr(auth, "da1")
    r = auth.client.post("/projects", json={"name": "new1"}, headers=h)
    assert r.status_code == 201 and r.json()["domain_id"] == "d1"
    assert auth.client.post("/projects", json={"name": "new2", "domain_id": "d2"}, headers=h).status_code == 403
    r = auth.client.post("/projects", json={"name": "new3", "domain_id": "d2"}, headers=hdr(auth, "sys"))
    assert r.status_code == 201 and r.json()["domain_id"] == "d2"
    assert auth.client.post("/projects", json={"name": "new4", "domain_id": "nope"}, headers=hdr(auth, "sys")).status_code == 404

def test_domain_admin_cannot_set_quota(auth):
    assert auth.client.put("/projects/p1/quota", json={"max_instances": 1}, headers=hdr(auth, "da1")).status_code == 403

def test_domain_admin_creates_user_only_in_own_domain(auth):
    h = hdr(auth, "da1")
    body = {"username": "n1", "email": "n1@x", "password": "pw", "project_id": "p1b"}
    r = auth.client.post("/users", json=body, headers=h)
    assert r.status_code == 201 and r.json()["domain_id"] == "d1"
    r = auth.client.post("/users", json={**body, "username": "n2", "email": "n2@x", "project_id": "p2"}, headers=h)
    assert r.status_code == 404

def test_domain_admin_cannot_create_admin_or_domain_admin_user(auth):
    h = hdr(auth, "da1")
    body = {"username": "n1", "email": "n1@x", "password": "pw", "project_id": "p1"}
    assert auth.client.post("/users", json={**body, "role": "admin"}, headers=h).status_code == 403
    assert auth.client.post("/users", json={**body, "is_domain_admin": True}, headers=h).status_code == 403
    r = auth.client.post("/users", json={**body, "is_domain_admin": True}, headers=hdr(auth, "sys"))
    assert r.status_code == 201 and r.json()["is_domain_admin"] is True

def test_domain_admin_cannot_escalate_self_via_put_user(auth):
    h = hdr(auth, "da1")
    assert auth.client.put("/users/da1", json={"role": "admin"}, headers=h).status_code == 403
    assert auth.client.put("/users/da1", json={"is_domain_admin": False}, headers=h).status_code == 403
    assert auth.client.put("/users/u1", json={"email": "new@x"}, headers=h).status_code == 200

def test_domain_admin_users_are_scoped(auth):
    h = hdr(auth, "da1")
    names = {u["username"] for u in auth.client.get("/users", headers=h).json()["items"]}
    assert names == {"da1", "u1"}
    assert auth.client.get("/users/u2", headers=h).status_code == 404
    assert auth.client.put("/users/u2", json={"email": "z@x"}, headers=h).status_code == 404
    assert auth.client.delete("/users/u2", headers=h).status_code == 404
    assert auth.client.delete("/users/u1", headers=h).status_code == 204

def test_domain_admin_cannot_create_or_delete_domain(auth):
    h = hdr(auth, "da1")
    assert auth.client.post("/v3/domains", json={"name": "evil"}, headers=h).status_code == 403
    assert auth.client.delete("/v3/domains/d2", headers=h).status_code == 403
    assert auth.client.patch("/v3/domains/d1", json={"enabled": False}, headers=h).status_code == 403

def test_domain_list_for_domain_admin_only_own(auth):
    ids = {d["id"] for d in auth.client.get("/v3/domains", headers=hdr(auth, "da1")).json()["domains"]}
    assert ids == {"d1"}
    assert auth.client.get("/v3/domains/d2", headers=hdr(auth, "da1")).status_code == 404

def test_delete_domain_with_members_is_409(auth):
    assert auth.client.delete("/v3/domains/d1", headers=hdr(auth, "sys")).status_code == 409
    assert auth.client.post("/v3/domains", json={"name": "empty"}, headers=hdr(auth, "sys")).status_code == 201

def test_regular_member_cannot_use_domain_admin_endpoints(auth):
    h = hdr(auth, "u1")
    assert auth.client.post("/projects", json={"name": "x"}, headers=h).status_code == 403
    assert auth.client.post("/users", json={"username": "q", "email": "q@x", "password": "pw", "project_id": "p1"}, headers=h).status_code == 403


# ---- groups, role assignments, login/switch ------------------------------------------------------

def test_role_assignment_across_domains_is_409(auth):
    r = auth.client.put("/projects/p2/users/u1/roles/reader", headers=hdr(auth, "sys"))
    assert r.status_code == 409
    assert auth.client.put("/projects/p1b/users/u1/roles/reader", headers=hdr(auth, "sys")).status_code == 204

def test_domain_admin_assigns_role_only_inside_own_domain(auth):
    h = hdr(auth, "da1")
    assert auth.client.put("/projects/p1b/users/u1/roles/reader", headers=h).status_code == 204
    assert auth.client.put("/projects/p2/users/u2/roles/reader", headers=h).status_code == 404
    assert auth.client.put("/projects/p1b/users/u2/roles/reader", headers=h).status_code == 404
    assert auth.client.put("/projects/p1b/users/u1/roles/admin", headers=h).status_code == 403
    items = auth.client.get("/role-assignments", headers=h).json()["items"]
    assert {i["user_id"] for i in items} == {"u1"}

def _group(auth, name, project_login):
    r = auth.client.post("/groups", json={"name": name}, headers=hdr(auth, project_login))
    assert r.status_code == 201, r.text
    return r.json()["id"]

def test_group_member_across_domains_is_409(auth):
    gid = _group(auth, "g1", "da1")
    h = hdr(auth, "da1")
    assert auth.client.put(f"/groups/{gid}/members/u1", headers=h).status_code == 200
    assert auth.client.put(f"/groups/{gid}/members/u2", headers=h).status_code == 404
    assert auth.client.put(f"/groups/{gid}/members/u2", headers=hdr(auth, "sys")).status_code == 409

def test_domain_admin_cannot_touch_foreign_group_by_id(auth):
    gid = _group(auth, "g2", "da2")
    h = hdr(auth, "da1")
    assert auth.client.delete(f"/groups/{gid}", headers=h).status_code == 404
    assert auth.client.put(f"/groups/{gid}/members/u1", headers=h).status_code == 404
    names = {g["name"] for g in auth.client.get("/groups", headers=h).json()["items"]}
    assert "g2" not in names

def test_switch_project_across_domains_is_403(auth):
    h = hdr(auth, "u1")
    assert auth.client.post("/session/switch-project", json={"project_id": "p2"}, headers=h).status_code == 403
    with auth.db.SessionLocal() as db:   # even a stray assignment from another domain must not grant access
        db.add(auth.models.RoleAssignment(user_id="u1", project_id="p2", role="member"))
        db.commit()
    assert auth.client.post("/session/switch-project", json={"project_id": "p2"}, headers=hdr(auth, "u1")).status_code == 403
    assert login(auth, "u1", "p-two").status_code == 403

def test_system_admin_can_switch_to_any_domain(auth):
    r = auth.client.post("/session/switch-project", json={"project_id": "p2"}, headers=hdr(auth, "sys"))
    assert r.status_code == 200

def test_login_in_disabled_domain_is_403(auth):
    assert auth.client.patch("/v3/domains/d2", json={"enabled": False}, headers=hdr(auth, "sys")).status_code == 200
    assert login(auth, "u2").status_code == 403
    assert login(auth, "u1").status_code == 201

