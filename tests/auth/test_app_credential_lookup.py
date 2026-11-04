"""An application credential is reached through its own user only, and a login by name picks the credential whose secret fits."""
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
            m.User(id="u1", username="u1", email="u1@x", password_hash=pw, project_id="p1", domain_id="default", role="member"),
            m.User(id="u2", username="u2", email="u2@x", password_hash=pw, project_id="p1", domain_id="default", role="member"),
        ])
        db.commit()
    a.client = TestClient(a.app)
    return a


def hdr(auth, username):
    r = auth.client.post("/auth/tokens", json={"username": username, "password": "pw"})
    return {"X-Auth-Token": r.json()["token"]}


def make_cred(auth, user, name="ci"):
    r = auth.client.post(f"/v3/users/{user}/application_credentials", json={"name": name}, headers=hdr(auth, user))
    assert r.status_code == 201, r.text
    return r.json()


def exists(auth, cred_id):
    with auth.db.SessionLocal() as db:
        return db.get(auth.models.ApplicationCredential, cred_id) is not None


# ── reaching a credential through its own user only ──────────────────────────

def test_a_user_cannot_show_another_users_credential_by_id(auth):
    theirs = make_cred(auth, "u2")

    r = auth.client.get(f"/v3/users/u1/application_credentials/{theirs['id']}", headers=hdr(auth, "u1"))

    assert r.status_code == 404, r.text


def test_a_user_cannot_delete_another_users_credential_by_id(auth):
    theirs = make_cred(auth, "u2")

    r = auth.client.delete(f"/v3/users/u1/application_credentials/{theirs['id']}", headers=hdr(auth, "u1"))

    assert r.status_code == 404, r.text
    assert exists(auth, theirs["id"])


def test_an_admin_with_the_wrong_user_ref_does_not_reach_the_credential_either(auth):
    theirs = make_cred(auth, "u2")

    shown = auth.client.get(f"/v3/users/u1/application_credentials/{theirs['id']}", headers=hdr(auth, "adm"))
    deleted = auth.client.delete(f"/v3/users/u1/application_credentials/{theirs['id']}", headers=hdr(auth, "adm"))

    assert shown.status_code == 404 and deleted.status_code == 404
    assert exists(auth, theirs["id"])


def test_a_user_still_shows_and_deletes_their_own_credential_by_id_and_by_name(auth):
    mine = make_cred(auth, "u1")

    by_id = auth.client.get(f"/v3/users/u1/application_credentials/{mine['id']}", headers=hdr(auth, "u1"))
    by_name = auth.client.get("/v3/users/u1/application_credentials/ci", headers=hdr(auth, "u1"))
    deleted = auth.client.delete(f"/v3/users/u1/application_credentials/{mine['id']}", headers=hdr(auth, "u1"))

    assert by_id.status_code == 200 and by_name.status_code == 200 and by_id.json()["id"] == mine["id"]
    assert deleted.status_code == 204 and not exists(auth, mine["id"])


def test_an_admin_shows_and_deletes_a_users_credential_through_that_user(auth):
    theirs = make_cred(auth, "u2")

    shown = auth.client.get(f"/v3/users/u2/application_credentials/{theirs['id']}", headers=hdr(auth, "adm"))
    deleted = auth.client.delete(f"/v3/users/u2/application_credentials/{theirs['id']}", headers=hdr(auth, "adm"))

    assert shown.status_code == 200 and deleted.status_code == 204 and not exists(auth, theirs["id"])


# ── login by name ────────────────────────────────────────────────────────────

def login_by_name(auth, secret, name="ci"):
    return auth.client.post("/auth/tokens/application-credential", json={
        "application_credential_name": name, "application_credential_secret": secret})


@pytest.mark.parametrize("user", ["u1", "u2"])
def test_login_by_name_finds_the_credential_whose_secret_fits(auth, user):
    # two users own a credential called "ci"; whichever row the database returns first, each secret logs in its owner
    creds = {u: make_cred(auth, u) for u in ("u1", "u2")}

    r = login_by_name(auth, creds[user]["secret"])

    assert r.status_code == 201, r.text
    assert auth.tokens._decode_token(r.json()["token"])["sub"] == user


def test_login_by_name_with_a_wrong_secret_is_refused(auth):
    make_cred(auth, "u1")
    make_cred(auth, "u2")

    assert login_by_name(auth, "not-a-secret").status_code == 401


def test_login_by_name_without_any_credential_is_refused(auth):
    assert login_by_name(auth, "whatever", name="nothing").status_code == 401


def test_login_by_name_skips_a_disabled_credential(auth):
    creds = {u: make_cred(auth, u) for u in ("u1", "u2")}
    with auth.db.SessionLocal() as db:
        db.get(auth.models.ApplicationCredential, creds["u1"]["id"]).enabled = False
        db.commit()

    assert login_by_name(auth, creds["u1"]["secret"]).status_code == 401
    assert login_by_name(auth, creds["u2"]["secret"]).status_code == 201
