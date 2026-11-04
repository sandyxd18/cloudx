"""The roles of an application credential must be roles its user actually holds, at creation and at every login."""
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
            m.User(id="rd", username="rd", email="rd@x", password_hash=pw, project_id="p1", domain_id="default", role="reader"),
        ])
        db.commit()
    a.client = TestClient(a.app)
    return a


def hdr(auth, username):
    r = auth.client.post("/auth/tokens", json={"username": username, "password": "pw"})
    return {"X-Auth-Token": r.json()["token"]}


def create(auth, user, as_user=None, **body):
    body.setdefault("name", "ci")
    return auth.client.post(f"/v3/users/{user}/application_credentials", json=body, headers=hdr(auth, as_user or user))


def login(auth, cred_id, secret):
    return auth.client.post("/auth/tokens/application-credential", json={
        "application_credential_id": cred_id, "application_credential_secret": secret})


def claims(auth, response):
    return auth.tokens._decode_token(response.json()["token"])


def add_legacy_credential(auth, roles, secret="legacy-secret"):
    """A credential row as an older version could have stored it: roles were never checked."""
    with auth.db.SessionLocal() as db:
        ac = auth.models.ApplicationCredential(
            name="legacy", user_id="u1", project_id="p1", roles=roles, secret_hash=auth.tokens._hash_password(secret))
        db.add(ac)
        db.commit()
        return ac.id


# ── creation ─────────────────────────────────────────────────────────────────

def test_member_cannot_create_an_admin_credential(auth):
    r = create(auth, "u1", roles=["admin"])

    assert r.status_code == 403, r.text
    with auth.db.SessionLocal() as db:
        assert db.query(auth.models.ApplicationCredential).count() == 0


def test_member_can_create_member_and_reader_credentials(auth):
    r = create(auth, "u1", roles=["member", "reader"])

    assert r.status_code == 201, r.text
    assert r.json()["roles"] == ["member", "reader"]
    token = claims(auth, login(auth, r.json()["id"], r.json()["secret"]))
    assert token["role"] == "member" and token["roles"] == ["member", "reader"]


def test_admin_can_create_an_admin_credential(auth):
    r = create(auth, "adm", roles=["admin"])

    assert r.status_code == 201, r.text
    assert claims(auth, login(auth, r.json()["id"], r.json()["secret"]))["role"] == "admin"


def test_an_admin_creating_for_a_member_is_checked_against_the_member(auth):
    r = create(auth, "u1", as_user="adm", roles=["admin"])

    assert r.status_code == 403, r.text


@pytest.mark.parametrize("roles", [["superuser"], ["member", "member"], []])
def test_invalid_roles_are_refused(auth, roles):
    r = create(auth, "u1", roles=roles)

    assert r.status_code == 400, r.text


@pytest.mark.parametrize("user,expected", [("u1", ["member"]), ("adm", ["member"]), ("rd", ["reader"])])
def test_omitted_roles_default_to_member_or_the_users_own_role(auth, user, expected):
    r = create(auth, user, as_user="adm")

    assert r.status_code == 201, r.text
    assert r.json()["roles"] == expected


# ── login ────────────────────────────────────────────────────────────────────

def test_login_trims_the_roles_to_what_the_user_still_holds(auth):
    r = create(auth, "adm", roles=["admin", "member"])
    with auth.db.SessionLocal() as db:
        db.get(auth.models.User, "adm").role = "member"
        db.commit()

    token = claims(auth, login(auth, r.json()["id"], r.json()["secret"]))

    assert token["role"] == "member" and token["roles"] == ["member"]


def test_a_legacy_admin_credential_of_a_member_cannot_log_in(auth):
    cred_id = add_legacy_credential(auth, "admin")

    assert login(auth, cred_id, "legacy-secret").status_code == 401


def test_a_legacy_credential_keeps_only_the_roles_its_user_holds(auth):
    cred_id = add_legacy_credential(auth, "admin,member")

    token = claims(auth, login(auth, cred_id, "legacy-secret"))

    assert token["role"] == "member" and token["roles"] == ["member"]


def test_a_legacy_credential_with_an_unknown_role_name_cannot_log_in(auth):
    cred_id = add_legacy_credential(auth, "superuser")

    assert login(auth, cred_id, "legacy-secret").status_code == 401
