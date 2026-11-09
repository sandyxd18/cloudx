from fastapi.testclient import TestClient
from sqlalchemy import select

from tests.stack import fresh_service


def _setup(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'auth_del.sqlite'}")
    m = fresh_service("auth", monkeypatch, tmp_path)
    admin = {"project_id": "p-admin", "role": "admin", "username": "admin", "sub": "u-admin", "roles": ["admin"],
             "scope_type": "project"}
    m.app.dependency_overrides[m._require_token] = lambda: admin
    m.app.dependency_overrides[m._require_admin] = lambda: admin
    with m.SessionLocal() as db:
        db.add_all([
            m.Project(id="p-admin", name="admin"),
            m.Project(id="p1", name="t1"),
            m.User(id="u-admin", username="admin", email="a@x", password_hash="x", project_id="p-admin", role="admin"),
            m.User(id="u1", username="u1", email="u1@x", password_hash="x", project_id="p1"),
            m.User(id="u2", username="u2", email="u2@x", password_hash="x", project_id="p-admin"),
        ])
        db.commit()
    return m, TestClient(m.app)


def _rows(m, model, **where):
    with m.SessionLocal() as db:
        return list(db.scalars(select(model).filter_by(**where)))


def test_delete_user_removes_its_credentials_keys_and_memberships(monkeypatch, tmp_path):
    m, c = _setup(monkeypatch, tmp_path)
    with m.SessionLocal() as db:
        db.add_all([
            m.Group(id="g1", name="g", project_id="p-admin"),
            m.GroupMember(group_id="g1", user_id="u1"),
            m.GroupMember(group_id="g1", user_id="u2"),
            m.ApplicationCredential(id="c1", name="c", user_id="u1", project_id="p1", secret_hash="x"),
            m.ApplicationCredential(id="c2", name="c", user_id="u2", project_id="p-admin", secret_hash="x"),
            m.AccessKey(id="k1", access_key_id="AK1", secret_key_hash="x", user_id="u1", project_id="p1"),
            m.Trust(id="t1", trustor_user_id="u1", trustee_user_id="u2", project_id="p1", roles="member"),
        ])
        db.commit()
    assert c.delete("/users/u1").status_code == 204
    assert _rows(m, m.User, id="u1") == []
    assert _rows(m, m.ApplicationCredential, user_id="u1") == []
    assert _rows(m, m.AccessKey, user_id="u1") == []
    assert _rows(m, m.GroupMember, user_id="u1") == []
    assert _rows(m, m.Trust, id="t1") == []
    # other users' rows stay
    assert len(_rows(m, m.ApplicationCredential, user_id="u2")) == 1
    assert len(_rows(m, m.GroupMember, user_id="u2")) == 1


def test_delete_project_with_users_is_a_conflict_and_deletes_nothing(monkeypatch, tmp_path):
    m, c = _setup(monkeypatch, tmp_path)
    with m.SessionLocal() as db:
        db.add(m.Group(id="g1", name="g", project_id="p1"))
        db.commit()
    r = c.delete("/projects/p1")
    assert r.status_code == 409
    assert "still has users" in r.json()["detail"]
    assert len(_rows(m, m.Project, id="p1")) == 1
    assert len(_rows(m, m.Group, id="g1")) == 1


def test_delete_empty_project_removes_its_groups_keys_and_credentials(monkeypatch, tmp_path):
    m, c = _setup(monkeypatch, tmp_path)
    with m.SessionLocal() as db:
        db.add_all([
            m.Group(id="g1", name="g", project_id="p1"),
            m.GroupMember(group_id="g1", user_id="u2"),
            m.ApplicationCredential(id="c3", name="c", user_id="u2", project_id="p1", secret_hash="x"),
            m.AccessKey(id="k3", access_key_id="AK3", secret_key_hash="x", user_id="u2", project_id="p1"),
        ])
        db.delete(db.get(m.User, "u1"))
        db.commit()
    assert c.delete("/projects/p1").status_code == 204
    assert _rows(m, m.Project, id="p1") == []
    assert _rows(m, m.Group, id="g1") == []
    assert _rows(m, m.GroupMember, group_id="g1") == []
    assert _rows(m, m.ApplicationCredential, id="c3") == []
    assert _rows(m, m.AccessKey, id="k3") == []
    assert len(_rows(m, m.User, id="u2")) == 1


def test_delete_group_with_members(monkeypatch, tmp_path):
    m, c = _setup(monkeypatch, tmp_path)
    with m.SessionLocal() as db:
        db.add_all([m.Group(id="g1", name="g", project_id="p-admin"), m.GroupMember(group_id="g1", user_id="u2")])
        db.commit()
    assert c.delete("/groups/g1").status_code == 204
    assert _rows(m, m.GroupMember, group_id="g1") == []
