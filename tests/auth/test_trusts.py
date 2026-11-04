"""Trusts: a trustor delegates some of their roles in one project to a trustee, who redeems the trust for a token."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from tests.stack import fresh_service


@pytest.fixture
def auth(monkeypatch, tmp_path):
    a = fresh_service("auth", monkeypatch, tmp_path)
    pw = a.tokens._hash_password("pw")
    with a.db.SessionLocal() as db:
        m = a.models
        db.add_all([m.Domain(id="default", name="Default"), m.Domain(id="d2", name="two")])
        db.flush()
        db.add_all([
            m.Project(id="p1", name="p-one", domain_id="default"),
            m.Project(id="p2", name="p-two", domain_id="default"),
            m.Project(id="pd2", name="p-d2", domain_id="d2"),
        ])
        db.flush()
        db.add_all([
            m.User(id="adm", username="adm", email="a@x", password_hash=pw, project_id="p1", domain_id="default", role="admin"),
            m.User(id="u1", username="u1", email="u1@x", password_hash=pw, project_id="p1", domain_id="default"),
            m.User(id="u2", username="u2", email="u2@x", password_hash=pw, project_id="p1", domain_id="default"),
            m.User(id="u3", username="u3", email="u3@x", password_hash=pw, project_id="p2", domain_id="default"),
            m.User(id="dadm", username="dadm", email="d@x", password_hash=pw, project_id="p1", domain_id="default", is_domain_admin=True),
            m.User(id="pa", username="pa", email="pa@x", password_hash=pw, project_id="p1", domain_id="default"),
            m.User(id="x1", username="x1", email="x1@x", password_hash=pw, project_id="pd2", domain_id="d2"),
        ])
        db.add(m.RoleAssignment(user_id="pa", project_id="p1", role="admin"))   # project admin only: User.role is member
        db.commit()
    a.client = TestClient(a.app)
    return a


def login(auth, username, **extra):
    r = auth.client.post("/auth/tokens", json={"username": username, "password": "pw", **extra})
    assert r.status_code == 201, r.text
    return {"X-Auth-Token": r.json()["token"]}


def set_role(auth, user, project, role):
    with auth.db.SessionLocal() as db:
        a = db.get(auth.models.RoleAssignment, (user, project))
        if role is None:
            if a:
                db.delete(a)
        elif a:
            a.role = role
        else:
            db.add(auth.models.RoleAssignment(user_id=user, project_id=project, role=role))
        db.commit()


def make_trust(auth, as_user="u1", **over):
    body = {"trustee_user_id": "u2", "project_id": "p1", "roles": ["member"], **over}
    return auth.client.post("/v3/OS-TRUST/trusts", json=body, headers=login(auth, as_user))


def redeem(auth, trust_id, as_user="u2", headers=None):
    return auth.client.post("/auth/tokens/trust", json={"trust_id": trust_id}, headers=headers or login(auth, as_user))


def claims(auth, r):
    return auth.tokens._decode_token(r.json()["token"])


def edit_trust(auth, trust_id, **fields):
    with auth.db.SessionLocal() as db:
        t = db.get(auth.models.Trust, trust_id)
        for k, v in fields.items():
            setattr(t, k, v)
        db.commit()


def remaining(auth, trust_id):
    with auth.db.SessionLocal() as db:
        return db.get(auth.models.Trust, trust_id).remaining_uses


# ── creating ──────────────────────────────────────────────────────────────────
def test_create_requires_trustor_to_hold_the_roles(auth):
    assert make_trust(auth, roles=["admin"]).status_code == 403          # u1 is only a member
    assert make_trust(auth, roles=["member", "admin"]).status_code == 403
    assert make_trust(auth, roles=["reader"]).status_code == 201         # a lower role is held too
    set_role(auth, "u1", "p1", "admin")
    r = make_trust(auth, roles=["admin", "member"])
    assert r.status_code == 201, r.text
    assert r.json()["roles"] == ["admin", "member"]


def test_create_validates_the_body(auth):
    assert make_trust(auth, roles=[]).status_code == 400
    assert make_trust(auth, roles=["superuser"]).status_code == 400
    assert make_trust(auth, roles=["member", "member"]).status_code == 400
    assert make_trust(auth, trustee_user_id="u1").status_code == 400     # trustor cannot be the trustee
    assert make_trust(auth, trustee_user_id="nobody").status_code == 404
    assert make_trust(auth, expires_at="not-a-date").status_code == 400
    assert make_trust(auth, expires_at="2001-01-01T00:00:00Z").status_code == 400   # already past
    assert make_trust(auth, remaining_uses=0).status_code == 422


def test_create_defaults_to_the_project_of_the_callers_token(auth):
    r = make_trust(auth, project_id=None)
    assert r.status_code == 201 and r.json()["project_id"] == "p1"
    assert r.json()["trustor_user_id"] == "u1" and r.json()["trustee_user_id"] == "u2"


def test_domain_scoped_caller_must_name_a_project(auth):
    with auth.db.SessionLocal() as db:
        db.get(auth.models.User, "adm").is_domain_admin = True
        db.commit()
    h = login(auth, "adm", scope={"domain": "default"})
    r = auth.client.post("/v3/OS-TRUST/trusts", json={"trustee_user_id": "u2", "roles": ["member"]}, headers=h)
    assert r.status_code == 400


def test_trustor_must_be_a_member_of_the_project(auth):
    # u3 lives in p2 and holds the default role "member"; that must not let u3 delegate roles in p1
    assert make_trust(auth, as_user="u3", project_id="p1").status_code == 403
    set_role(auth, "u3", "p1", "member")
    assert make_trust(auth, as_user="u3", project_id="p1").status_code == 201


def test_trustee_in_another_domain_is_refused_unless_the_caller_is_admin(auth):
    assert make_trust(auth, trustee_user_id="x1").status_code == 403
    assert make_trust(auth, as_user="adm", trustee_user_id="x1", roles=["member"]).status_code == 201


def test_disabled_trustee_is_refused(auth):
    with auth.db.SessionLocal() as db:
        db.get(auth.models.User, "u2").enabled = False
        db.commit()
    assert make_trust(auth).status_code == 403


def test_create_list_show_delete(auth):
    exp = (auth.models.utcnow() + timedelta(days=1)).isoformat() + "Z"
    r = make_trust(auth, expires_at=exp, remaining_uses=3)
    assert r.status_code == 201, r.text
    t = r.json()
    assert t["id"] and t["remaining_uses"] == 3 and t["expires_at"].startswith(exp[:16]) and t["created_at"]
    h1, h2 = login(auth, "u1"), login(auth, "u2")

    for h in (h1, h2):   # trustor and trustee both see it
        lst = auth.client.get("/v3/OS-TRUST/trusts", headers=h).json()["trusts"]
        assert [x["id"] for x in lst] == [t["id"]]
        assert auth.client.get(f"/v3/OS-TRUST/trusts/{t['id']}", headers=h).json() == t
    assert auth.client.get("/v3/OS-TRUST/trusts", headers=login(auth, "u3")).json()["trusts"] == []
    assert auth.client.get(f"/v3/OS-TRUST/trusts/{t['id']}", headers=login(auth, "u3")).status_code == 404

    assert auth.client.delete(f"/v3/OS-TRUST/trusts/{t['id']}", headers=h1).status_code == 204
    # revoked trusts are hidden
    assert auth.client.get("/v3/OS-TRUST/trusts", headers=h1).json()["trusts"] == []
    assert auth.client.get(f"/v3/OS-TRUST/trusts/{t['id']}", headers=h1).status_code == 404
    assert auth.client.get("/v3/OS-TRUST/trusts/nope", headers=h1).status_code == 404
    assert auth.client.delete("/v3/OS-TRUST/trusts/nope", headers=h1).status_code == 404


def test_only_trustor_or_admin_can_delete(auth):
    t1 = make_trust(auth).json()
    assert auth.client.delete(f"/v3/OS-TRUST/trusts/{t1['id']}", headers=login(auth, "u2")).status_code == 403
    assert auth.client.delete(f"/v3/OS-TRUST/trusts/{t1['id']}", headers=login(auth, "u3")).status_code == 403
    assert redeem(auth, t1["id"]).status_code == 201   # still alive
    assert auth.client.delete(f"/v3/OS-TRUST/trusts/{t1['id']}", headers=login(auth, "adm")).status_code == 204
    assert redeem(auth, t1["id"]).status_code == 401

    t2 = make_trust(auth).json()
    assert auth.client.delete(f"/v3/OS-TRUST/trusts/{t2['id']}", headers=login(auth, "u1")).status_code == 204


def test_admin_sees_every_trust(auth):
    t = make_trust(auth).json()
    ids = [x["id"] for x in auth.client.get("/v3/OS-TRUST/trusts", headers=login(auth, "adm")).json()["trusts"]]
    assert ids == [t["id"]]


# ── redeeming ─────────────────────────────────────────────────────────────────
def test_token_is_limited_to_the_trust_roles_and_sub_is_the_trustor(auth):
    set_role(auth, "u1", "p1", "admin")
    t = make_trust(auth, roles=["member", "reader"]).json()

    r = redeem(auth, t["id"])

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["method"] == "trust" and body["expires_in"] == auth.config.TOKEN_EXPIRE_H * 3600
    c = claims(auth, r)
    assert (c["sub"], c["username"], c["project_id"], c["project_name"]) == ("u1", "u1", "p1", "p-one")
    assert (c["role"], c["roles"]) == ("member", ["member", "reader"])   # not the trustor's admin
    assert (c["trust_id"], c["trustee_id"], c["scope_type"]) == (t["id"], "u2", "project")


def test_ordinary_tokens_carry_no_trust_claims(auth):
    c = auth.tokens._decode_token(login(auth, "u1")["X-Auth-Token"])
    assert "trust_id" not in c and "trustee_id" not in c


def test_expired_trust_is_401(auth):
    t = make_trust(auth).json()
    edit_trust(auth, t["id"], expires_at=auth.models.utcnow() - timedelta(minutes=1))
    assert redeem(auth, t["id"]).status_code == 401


def test_token_from_a_short_trust_does_not_outlive_the_trust(auth):
    t = make_trust(auth).json()
    ends = auth.models.utcnow() + timedelta(seconds=300)
    edit_trust(auth, t["id"], expires_at=ends)

    r = redeem(auth, t["id"])

    assert r.status_code == 201, r.text
    exp = datetime.fromtimestamp(claims(auth, r)["exp"], tz=timezone.utc).replace(tzinfo=None)
    assert exp <= ends
    assert 0 < r.json()["expires_in"] <= 300


def test_trust_without_expiry_gives_the_default_lifetime(auth):
    t = make_trust(auth).json()
    edit_trust(auth, t["id"], expires_at=None)

    r = redeem(auth, t["id"])

    assert r.json()["expires_in"] == auth.config.TOKEN_EXPIRE_H * 3600
    assert claims(auth, r)["exp"] - claims(auth, r)["iat"] == auth.config.TOKEN_EXPIRE_H * 3600


def test_trust_longer_than_the_default_lifetime_keeps_the_default(auth):
    t = make_trust(auth).json()
    edit_trust(auth, t["id"], expires_at=auth.models.utcnow() + timedelta(days=30))

    assert redeem(auth, t["id"]).json()["expires_in"] == auth.config.TOKEN_EXPIRE_H * 3600


def test_token_is_rejected_once_the_trust_expiry_has_passed(auth):
    # the module clock is `tokens.utcnow`: a token minted for a trust that ended 5 s ago is already dead
    ended = auth.models.utcnow() - timedelta(seconds=5)
    token = auth.tokens._create_token(
        auth.models.User(id="u-1", username="alice", project_id="p-1", role="member"), expires_at=ended)

    with pytest.raises(HTTPException) as exc:
        auth.tokens._decode_token(token)
    assert exc.value.status_code == 401


def test_revoked_trust_is_401(auth):
    t = make_trust(auth).json()
    edit_trust(auth, t["id"], revoked=True)
    assert redeem(auth, t["id"]).status_code == 401


def test_unknown_trust_is_401_not_404(auth):
    assert redeem(auth, "no-such-trust").status_code == 401


def test_other_user_cannot_use_the_trust(auth):
    t = make_trust(auth).json()
    assert redeem(auth, t["id"], as_user="u3").status_code == 403
    assert redeem(auth, t["id"], as_user="u1").status_code == 403   # not even the trustor
    assert redeem(auth, t["id"], as_user="adm").status_code == 403


def test_disabled_trustor_is_401(auth):
    t = make_trust(auth).json()
    with auth.db.SessionLocal() as db:
        db.get(auth.models.User, "u1").enabled = False
        db.commit()
    assert redeem(auth, t["id"]).status_code == 401


def test_disabled_project_or_domain_is_401(auth):
    t = make_trust(auth).json()
    h = login(auth, "u2")     # logging in is impossible once the project or domain is disabled
    with auth.db.SessionLocal() as db:
        db.get(auth.models.Project, "p1").enabled = False
        db.commit()
    assert redeem(auth, t["id"], headers=h).status_code == 401
    with auth.db.SessionLocal() as db:
        db.get(auth.models.Project, "p1").enabled = True
        db.get(auth.models.Domain, "default").enabled = False
        db.commit()
    assert redeem(auth, t["id"], headers=h).status_code == 401


def test_trustor_no_longer_a_member_is_401(auth):
    set_role(auth, "u3", "p1", "member")
    t = make_trust(auth, as_user="u3").json()
    set_role(auth, "u3", "p1", None)    # u3's home project is p2: no longer a member of p1
    assert redeem(auth, t["id"]).status_code == 401


def test_role_removed_from_trustor_shrinks_the_token_roles(auth):
    set_role(auth, "u1", "p1", "admin")
    t = make_trust(auth, roles=["admin", "member"]).json()
    assert claims(auth, redeem(auth, t["id"]))["roles"] == ["admin", "member"]

    set_role(auth, "u1", "p1", "member")      # the trustor is demoted
    c = claims(auth, redeem(auth, t["id"]))
    assert (c["role"], c["roles"]) == ("member", ["member"])

    set_role(auth, "u1", "p1", "reader")      # the trustor holds none of the trust's roles any more
    with auth.db.SessionLocal() as db:
        db.get(auth.models.User, "u1").role = "reader"
        db.commit()
    assert redeem(auth, t["id"]).status_code == 401


def test_remaining_uses_one_allows_exactly_one_token(auth):
    t = make_trust(auth, remaining_uses=1).json()
    assert redeem(auth, t["id"]).status_code == 201
    assert redeem(auth, t["id"]).status_code == 401
    assert remaining(auth, t["id"]) == 0

    t = make_trust(auth, remaining_uses=2).json()
    assert [redeem(auth, t["id"]).status_code for _ in range(3)] == [201, 201, 401]


def test_unlimited_trust_stays_unlimited(auth):
    t = make_trust(auth).json()
    assert redeem(auth, t["id"]).status_code == 201
    assert remaining(auth, t["id"]) is None


def test_a_failed_check_does_not_consume_a_use(auth):
    t = make_trust(auth, remaining_uses=1).json()
    edit_trust(auth, t["id"], expires_at=auth.models.utcnow() - timedelta(minutes=1))
    assert redeem(auth, t["id"]).status_code == 401
    assert remaining(auth, t["id"]) == 1

    edit_trust(auth, t["id"], expires_at=None)
    assert redeem(auth, t["id"], as_user="u3").status_code == 403    # wrong caller
    assert remaining(auth, t["id"]) == 1
    with auth.db.SessionLocal() as db:
        db.get(auth.models.User, "u1").enabled = False
        db.commit()
    assert redeem(auth, t["id"]).status_code == 401                  # disabled trustor
    assert remaining(auth, t["id"]) == 1


def test_consume_use_is_atomic_across_two_sessions(auth):
    """Both sessions read remaining_uses=1 before either writes: the conditional UPDATE lets exactly one win."""
    t = make_trust(auth, remaining_uses=1).json()
    a, b = auth.db.SessionLocal(), auth.db.SessionLocal()
    try:
        assert a.get(auth.models.Trust, t["id"]).remaining_uses == 1
        assert b.get(auth.models.Trust, t["id"]).remaining_uses == 1
        won_a = auth.trusts._consume_use(a, t["id"]); a.commit()
        won_b = auth.trusts._consume_use(b, t["id"]); b.commit()
    finally:
        a.close()
        b.close()
    assert (won_a, won_b) == (True, False)
    assert remaining(auth, t["id"]) == 0


def test_concurrent_redemptions_yield_exactly_one_token(auth):
    t = make_trust(auth, remaining_uses=1).json()
    heads = [login(auth, "u2") for _ in range(6)]
    barrier = threading.Barrier(len(heads))

    def go(h):
        barrier.wait()
        return redeem(auth, t["id"], headers=h).status_code

    with ThreadPoolExecutor(len(heads)) as pool:
        codes = list(pool.map(go, heads))

    assert sorted(codes) == [201] + [401] * 5, codes
    assert remaining(auth, t["id"]) == 0


# ── no redelegation ───────────────────────────────────────────────────────────
def test_trust_token_cannot_create_a_trust(auth):
    t = make_trust(auth, roles=["member"]).json()
    h = {"X-Auth-Token": redeem(auth, t["id"]).json()["token"]}
    r = auth.client.post("/v3/OS-TRUST/trusts", json={"trustee_user_id": "u3", "project_id": "p1", "roles": ["member"]}, headers=h)
    assert r.status_code == 403


def test_trust_token_cannot_chain_another_trust_token(auth):
    t = make_trust(auth).json()
    h = {"X-Auth-Token": redeem(auth, t["id"]).json()["token"]}
    # even though this token's sub is the trustor, it is not a trustee-side login
    assert auth.client.post("/auth/tokens/trust", json={"trust_id": t["id"]}, headers=h).status_code == 403


TRUST_ONLY = "trust tokens cannot modify identity"


def forge(auth, user, **claim_overrides):
    """A real token for `user` with claims changed afterwards, signed with the service key."""
    from jose import jwt
    c = auth.tokens._decode_token(login(auth, user)["X-Auth-Token"])
    c.update(claim_overrides)
    return {"X-Auth-Token": jwt.encode(c, auth.config.SECRET_KEY, algorithm=auth.config.ALGORITHM)}


def admin_trust_token(auth):
    """(trust, trust token headers): an admin trustor delegating the admin role to u2."""
    t = make_trust(auth, as_user="adm", roles=["admin"]).json()
    return t, {"X-Auth-Token": redeem(auth, t["id"]).json()["token"]}


def mutating_calls(auth, trust_id, group_id, cred_id):
    """name -> (call(headers), user whose ordinary token must succeed, expected status for that token)."""
    c = auth.client
    return {
        "post_users": (lambda h: c.post("/users", json={"username": "n1", "email": "n@x", "password": "pw", "project_id": "p1"}, headers=h), "adm", 201),
        "put_users_role": (lambda h: c.put("/users/u3", json={"role": "reader"}, headers=h), "adm", 200),
        "put_users_password": (lambda h: c.put("/users/u3", json={"password": "new"}, headers=h), "adm", 200),
        "access_key": (lambda h: c.post("/access-keys", json={"name": "k"}, headers=h), "adm", 201),
        "app_cred_create": (lambda h: c.post("/v3/users/adm/application_credentials", json={"name": "k2"}, headers=h), "adm", 201),
        "app_cred_rotate": (lambda h: c.post(f"/v3/users/adm/application_credentials/{cred_id}/rotate", headers=h), "adm", 200),
        "switch_project": (lambda h: c.post("/session/switch-project", json={"project_id": "p2"}, headers=h), "adm", 200),
        "role_assignment": (lambda h: c.put("/projects/p1/users/u3/roles/member", headers=h), "adm", 204),
        "project_create": (lambda h: c.post("/projects", json={"name": "newp"}, headers=h), "adm", 201),
        "group_member_add": (lambda h: c.put(f"/groups/{group_id}/members/u3", headers=h), "adm", 200),
        "trust_create": (lambda h: c.post("/v3/OS-TRUST/trusts", json={"trustee_user_id": "u3", "project_id": "p1", "roles": ["member"]}, headers=h), "adm", 201),
        "trust_exchange": (lambda h: c.post("/auth/tokens/trust", json={"trust_id": trust_id}, headers=h), "u2", 201),
    }


def test_trust_token_is_refused_on_every_mutating_auth_route_but_ordinary_tokens_are_not(auth):
    t, trust_h = admin_trust_token(auth)
    adm = login(auth, "adm")
    group_id = auth.client.post("/groups", json={"name": "g"}, headers=adm).json()["id"]
    cred_id = auth.client.post("/v3/users/adm/application_credentials", json={"name": "k"}, headers=adm).json()["id"]
    calls = mutating_calls(auth, t["id"], group_id, cred_id)

    for name, (call, _, _) in calls.items():      # all refused first, so nothing below depends on a side effect
        r = call(trust_h)
        assert (name, r.status_code, r.json()["detail"]) == (name, 403, TRUST_ONLY)
    for name, (call, user, ok) in calls.items():
        r = call(login(auth, user))
        assert (name, r.status_code) == (name, ok), r.text


def test_trust_token_needs_the_central_rule_even_for_a_reader_or_member_trust(auth):
    for roles in (["member"], ["reader"]):
        t = make_trust(auth, roles=roles).json()
        h = {"X-Auth-Token": redeem(auth, t["id"]).json()["token"]}
        assert auth.client.post("/session/switch-project", json={"project_id": "p1"}, headers=h).status_code == 403


def test_trust_token_can_read_and_log_out(auth):
    t, h = admin_trust_token(auth)
    assert auth.client.get("/users", headers=h).status_code == 200
    assert auth.client.get("/auth/tokens/validate", headers=h).status_code == 200
    assert auth.client.get("/v3/OS-TRUST/trusts", headers=h).status_code == 200
    assert auth.client.delete("/auth/tokens/current", headers=h).status_code == 204
    assert auth.client.get("/users", headers=h).status_code == 401    # revoked


@pytest.mark.parametrize("claim", [None, "", 0, False])
def test_an_empty_trust_id_claim_is_not_a_trust_token(auth, claim):
    h = forge(auth, "adm", trust_id=claim)
    r = auth.client.post("/projects", json={"name": f"np{claim!r}"}, headers=h)
    assert r.status_code == 201, r.text
    assert auth.client.post("/session/switch-project", json={"project_id": "p2"}, headers=h).status_code == 200


def test_a_truthy_trust_id_claim_makes_it_a_trust_token(auth):
    r = auth.client.post("/projects", json={"name": "np"}, headers=forge(auth, "adm", trust_id="anything"))
    assert (r.status_code, r.json()["detail"]) == (403, TRUST_ONLY)


@pytest.mark.parametrize("roles", [["member"], ["reader"]])
def test_trust_token_does_not_inherit_the_trustors_domain_admin(auth, roles):
    t = make_trust(auth, as_user="dadm", roles=roles).json()
    r = redeem(auth, t["id"])
    assert r.status_code == 201
    assert claims(auth, r)["domain_admin"] is False
    h = {"X-Auth-Token": r.json()["token"]}
    body = {"username": "n1", "email": "n@x", "password": "pw", "project_id": "p1"}
    assert auth.client.post("/users", json=body, headers=h).status_code == 403
    # the trustor's own token still has the right
    assert auth.client.post("/users", json=body, headers=login(auth, "dadm")).status_code == 201


def test_project_level_admin_is_not_a_system_admin_for_trusts(auth):
    # pa is admin only through a role assignment in p1; User.role is member
    assert make_trust(auth, as_user="pa", project_id="p2").status_code == 403           # not a member of p2
    assert make_trust(auth, as_user="pa", trustee_user_id="x1").status_code == 403      # cross-domain trustee
    assert make_trust(auth, as_user="pa", roles=["admin"]).status_code == 201           # own project, own domain: fine
    # a real (DB) admin still can
    assert make_trust(auth, as_user="adm", project_id="p2", roles=["admin"]).status_code == 201
    assert make_trust(auth, as_user="adm", trustee_user_id="x1").status_code == 201


# ── audit ─────────────────────────────────────────────────────────────────────
def test_token_issue_writes_an_audit_entry_with_both_ids(auth):
    t = make_trust(auth).json()
    with auth.db.SessionLocal() as db:
        assert db.query(auth.models.AuditLog).count() == 0     # creating a trust is not audited (no router audits)

    assert redeem(auth, t["id"]).status_code == 201

    rows = auth.client.get("/audit-logs?resource_type=trust", headers=login(auth, "adm")).json()["items"]
    assert len(rows) == 1
    row = rows[0]
    assert (row["action"], row["resource_type"], row["resource_id"], row["project_id"]) == ("trust.token", "trust", t["id"], "p1")
    assert row["user_id"] == "u2"
    assert json.loads(row["details"]) == {"trustor_user_id": "u1", "trustee_user_id": "u2"}


def test_failed_redemption_writes_no_audit_entry(auth):
    t = make_trust(auth).json()
    edit_trust(auth, t["id"], revoked=True)
    redeem(auth, t["id"])
    with auth.db.SessionLocal() as db:
        assert db.query(auth.models.AuditLog).count() == 0
