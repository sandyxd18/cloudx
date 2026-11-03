"""Rotating an application credential's secret, with an optional grace period for the old one."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from tests.stack import fresh_service


@pytest.fixture
def auth(monkeypatch, tmp_path):
    a = fresh_service("auth", monkeypatch, tmp_path)
    pw = a.tokens._hash_password("pw")
    with a.db.SessionLocal() as db:
        m = a.models
        db.add(m.Domain(id="default", name="Default"))
        db.flush()
        db.add(m.Project(id="p1", name="p-one", domain_id="default"))
        db.flush()
        db.add_all([
            m.User(id="adm", username="adm", email="a@x", password_hash=pw, project_id="p1", domain_id="default", role="admin"),
            m.User(id="u1", username="u1", email="u1@x", password_hash=pw, project_id="p1", domain_id="default"),
            m.User(id="u2", username="u2", email="u2@x", password_hash=pw, project_id="p1", domain_id="default"),
        ])
        db.commit()
    a.client = TestClient(a.app)
    return a


def hdr(auth, username):
    r = auth.client.post("/auth/tokens", json={"username": username, "password": "pw"})
    return {"X-Auth-Token": r.json()["token"]}


def make_cred(auth, user="u1", name="ci"):
    r = auth.client.post(f"/v3/users/{user}/application_credentials", json={"name": name}, headers=hdr(auth, user))
    assert r.status_code == 201, r.text
    return r.json()


def rotate(auth, cred, user="u1", as_user=None, body=None):
    kw = {} if body is None else {"json": body}
    return auth.client.post(f"/v3/users/{user}/application_credentials/{cred['id']}/rotate",
                            headers=hdr(auth, as_user or user), **kw)


def login(auth, cred, secret):
    return auth.client.post("/auth/tokens/application-credential", json={
        "application_credential_id": cred["id"], "application_credential_secret": secret})


def test_rotate_returns_a_working_new_secret(auth):
    cred = make_cred(auth)

    r = rotate(auth, cred)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == cred["id"] and body["secret"] and body["secret"] != cred["secret"]
    assert login(auth, cred, body["secret"]).status_code == 201
    # the secret is shown only by the rotate response
    shown = auth.client.get(f"/v3/users/u1/application_credentials/{cred['id']}", headers=hdr(auth, "u1")).json()
    assert "secret" not in shown and "previous_secret_hash" not in shown


def test_old_secret_fails_immediately_with_grace_zero(auth):
    cred = make_cred(auth)

    new = rotate(auth, cred).json()["secret"]  # no body at all means grace 0

    assert login(auth, cred, cred["secret"]).status_code == 401
    assert login(auth, cred, new).status_code == 201
    with auth.db.SessionLocal() as db:
        ac = db.get(auth.models.ApplicationCredential, cred["id"])
        assert ac.previous_secret_hash is None and ac.previous_valid_until is None


def test_explicit_zero_grace_also_kills_the_old_secret(auth):
    cred = make_cred(auth)
    rotate(auth, cred, body={"grace_seconds": 0})
    assert login(auth, cred, cred["secret"]).status_code == 401


def test_old_secret_works_until_grace_ends(auth, monkeypatch):
    cred = make_cred(auth)
    mod = auth.routers.app_credentials
    real_now = mod.utcnow()

    new = rotate(auth, cred, body={"grace_seconds": 600}).json()["secret"]

    assert login(auth, cred, cred["secret"]).status_code == 201
    assert login(auth, cred, new).status_code == 201
    monkeypatch.setattr(mod, "utcnow", lambda: real_now + timedelta(seconds=599))
    assert login(auth, cred, cred["secret"]).status_code == 201
    monkeypatch.setattr(mod, "utcnow", lambda: real_now + timedelta(seconds=601))
    assert login(auth, cred, cred["secret"]).status_code == 401
    assert login(auth, cred, new).status_code == 201


def test_rotating_twice_kills_the_first_secret(auth):
    cred = make_cred(auth)
    first = cred["secret"]
    second = rotate(auth, cred, body={"grace_seconds": 3600}).json()["secret"]
    assert login(auth, cred, first).status_code == 201  # inside the first grace window

    third = rotate(auth, cred, body={"grace_seconds": 3600}).json()["secret"]

    assert login(auth, cred, first).status_code == 401  # only the immediately previous secret survives
    assert login(auth, cred, second).status_code == 201
    assert login(auth, cred, third).status_code == 201


def test_rotating_with_grace_zero_inside_a_grace_window_ends_the_window(auth):
    cred = make_cred(auth)
    second = rotate(auth, cred, body={"grace_seconds": 3600}).json()["secret"]
    third = rotate(auth, cred).json()["secret"]
    assert login(auth, cred, cred["secret"]).status_code == 401
    assert login(auth, cred, second).status_code == 401
    assert login(auth, cred, third).status_code == 201


def test_a_previous_secret_cannot_revive_a_disabled_or_expired_credential(auth):
    cred = make_cred(auth)
    rotate(auth, cred, body={"grace_seconds": 3600})
    assert login(auth, cred, cred["secret"]).status_code == 201

    with auth.db.SessionLocal() as db:
        ac = db.get(auth.models.ApplicationCredential, cred["id"])
        ac.enabled = False
        db.commit()
    assert login(auth, cred, cred["secret"]).status_code == 401

    with auth.db.SessionLocal() as db:
        ac = db.get(auth.models.ApplicationCredential, cred["id"])
        ac.enabled = True
        ac.expires_at = auth.models.utcnow() - timedelta(seconds=5)
        db.commit()
    assert login(auth, cred, cred["secret"]).status_code == 401

    with auth.db.SessionLocal() as db:
        ac = db.get(auth.models.ApplicationCredential, cred["id"])
        ac.expires_at = None
        db.get(auth.models.User, "u1").enabled = False
        db.commit()
    assert login(auth, cred, cred["secret"]).status_code == 403


def test_a_wrong_secret_is_still_rejected_during_grace(auth):
    cred = make_cred(auth)
    rotate(auth, cred, body={"grace_seconds": 3600})
    assert login(auth, cred, "not-the-secret").status_code == 401


def test_rotate_by_another_member_is_403(auth):
    cred = make_cred(auth)

    r = rotate(auth, cred, as_user="u2")

    assert r.status_code == 403
    assert login(auth, cred, cred["secret"]).status_code == 201  # untouched


def test_an_admin_can_rotate_someone_elses_credential_by_name(auth):
    cred = make_cred(auth)
    r = auth.client.post("/v3/users/u1/application_credentials/ci/rotate", headers=hdr(auth, "adm"))
    assert r.status_code == 200 and login(auth, cred, r.json()["secret"]).status_code == 201


def test_a_credential_of_another_user_is_not_reachable_through_my_user_ref(auth):
    cred = make_cred(auth, user="u1")
    make_cred(auth, user="u2", name="other")

    r = auth.client.post(f"/v3/users/u2/application_credentials/{cred['id']}/rotate", headers=hdr(auth, "u2"))

    assert r.status_code == 404
    assert login(auth, cred, cred["secret"]).status_code == 201


def test_rotating_a_missing_credential_is_404(auth):
    r = auth.client.post("/v3/users/u1/application_credentials/nope/rotate", headers=hdr(auth, "u1"))
    assert r.status_code == 404


@pytest.mark.parametrize("grace", [-1, 86401])
def test_grace_out_of_range_is_422(auth, grace):
    cred = make_cred(auth)
    assert rotate(auth, cred, body={"grace_seconds": grace}).status_code == 422
    assert login(auth, cred, cred["secret"]).status_code == 201
