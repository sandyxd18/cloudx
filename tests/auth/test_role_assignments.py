"""Role assignments: a user has a per-project role, and an access key logs in to its own project."""
import pytest
from fastapi.testclient import TestClient

from tests.stack import fresh_service


@pytest.fixture
def auth(monkeypatch, tmp_path):
    a = fresh_service("auth", monkeypatch, tmp_path)
    with a.db.SessionLocal() as db:
        m = a.models
        db.add_all([m.Project(id="p1", name="one"), m.Project(id="p2", name="two")])
        db.flush()
        db.add(m.User(id="u1", username="bob", email="b@x", password_hash=a.tokens._hash_password("pw"),
                      project_id="p1", role="member"))
        db.commit()
    a.client = TestClient(a.app)
    return a


def login(auth, project=None):
    body = {"username": "bob", "password": "pw", **({"project": project} if project else {})}
    res = auth.client.post("/auth/tokens", json=body)
    return res, (auth.tokens._decode_token(res.json()["token"]) if res.status_code == 201 else None)


def assign(auth, role):
    with auth.db.SessionLocal() as db:
        db.merge(auth.models.RoleAssignment(user_id="u1", project_id="p2", role=role))
        db.commit()


def test_no_assignment_means_forbidden_in_other_project(auth):
    res, _ = login(auth, "two")
    assert res.status_code == 403


def test_assignment_grants_membership_and_role(auth):
    assign(auth, "reader")
    _, claims = login(auth, "two")
    assert claims["project_id"] == "p2" and claims["role"] == "reader"
    _, home = login(auth)
    assert home["project_id"] == "p1" and home["role"] == "member"


def test_access_key_token_is_scoped_to_the_key_project(auth):
    assign(auth, "reader")
    with auth.db.SessionLocal() as db:
        db.add(auth.models.AccessKey(access_key_id="CLDXTEST", secret_key_hash=auth.tokens._hash_password("s3"),
                                     user_id="u1", project_id="p2"))
        db.commit()
    res = auth.client.post("/auth/tokens/access-key", headers={"X-Access-Key-Id": "CLDXTEST", "X-Secret-Access-Key": "s3"})
    claims = auth.tokens._decode_token(res.json()["token"])
    assert claims["project_id"] == "p2" and claims["role"] == "reader"
