"""Users, projects and role assignments of auth-api are gated by the central policy (shared.policy)."""
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "services"))

from shared import policy  # noqa: E402
from tests.stack import fresh_service  # noqa: E402

# (method, path, action); the ids need not exist: the policy answers before the handler and before body validation
ROUTES = [
    ("POST", "/users", "identity:user:create"),
    ("GET", "/users", "identity:user:list"),
    ("DELETE", "/users/nobody", "identity:user:delete"),
    ("POST", "/projects", "identity:project:create"),
    ("PUT", "/projects/nobody", "identity:project:update"),
    ("DELETE", "/projects/nobody", "identity:project:delete"),
    ("PUT", "/projects/nobody/quota", "identity:project:quota-set"),
    ("PUT", "/projects/nobody/users/nobody/roles/member", "identity:role-assignment:create"),
    ("DELETE", "/projects/nobody/users/nobody/roles/member", "identity:role-assignment:delete"),
    ("GET", "/v3/domains", "identity:domain:list"),
    ("GET", "/v3/domains/nobody", "identity:domain:show"),
    ("GET", "/v3/domains/nobody/quota", "identity:domain:quota-show"),
    ("POST", "/v3/domains", "identity:domain:create"),
    ("PATCH", "/v3/domains/nobody", "identity:domain:update"),
    ("DELETE", "/v3/domains/nobody", "identity:domain:delete"),
    ("PUT", "/v3/domains/nobody/quota", "identity:domain:quota-set"),
    ("DELETE", "/v3/domains/nobody/quota", "identity:domain:quota-clear"),
    ("POST", "/v3/services", "identity:service:create"),
    ("DELETE", "/v3/services/nobody", "identity:service:delete"),
    ("POST", "/v3/endpoints", "identity:endpoint:create"),
    ("DELETE", "/v3/endpoints/nobody", "identity:endpoint:delete"),
    ("GET", "/audit-logs", "identity:audit-log:list"),
]
# spelled out, not taken from DEFAULT_RULES: these are the actions a domain admin could use through the old gates
DOMAIN_ADMIN_ACTIONS = {
    "identity:user:create", "identity:user:list", "identity:user:delete",
    "identity:domain:list", "identity:domain:show", "identity:domain:quota-show",
    "identity:project:create", "identity:project:update", "identity:project:delete",
    "identity:role-assignment:create", "identity:role-assignment:delete",
}
DOMAIN_ADMIN_ROUTES = [r for r in ROUTES if r[2] in DOMAIN_ADMIN_ACTIONS]
ADMIN_ONLY_ROUTES = [r for r in ROUTES if r[2] not in DOMAIN_ADMIN_ACTIONS]


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def pfile(tmp_path, monkeypatch):
    """A policy file that does not exist yet; every write gets a distinct mtime so tests never sleep."""
    path = tmp_path / "policy.yaml"
    monkeypatch.setenv("CLOUDX_POLICY_FILE", str(path))
    clock = Clock()
    monkeypatch.setattr(policy, "_monotonic", clock)
    policy.reset()

    class PolicyFile:
        tick = 0

        def write(self, text):
            path.write_text(text, encoding="utf-8")
            self.tick += 1
            stamp = 10**18 + self.tick * 10**9
            os.utime(path, ns=(stamp, stamp))
            clock.advance(policy.CHECK_INTERVAL + 1)  # the next request looks at the file

    yield PolicyFile()
    policy.reset()


@pytest.fixture
def auth(monkeypatch, tmp_path, pfile):
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
            m.User(id="da", username="da", email="da@x", password_hash=pw, project_id="p1", domain_id="default",
                   role="member", is_domain_admin=True),
            m.User(id="u1", username="u1", email="u1@x", password_hash=pw, project_id="p1", domain_id="default", role="member"),
        ])
        db.commit()
    a.client = TestClient(a.app)
    return a


def hdr(auth, username):
    r = auth.client.post("/auth/tokens", json={"username": username, "password": "pw"})
    return {"X-Auth-Token": r.json()["token"]}


def call(auth, method, path, username):
    return auth.client.request(method, path, headers=hdr(auth, username))


@pytest.mark.parametrize("method,path,action", ROUTES)
def test_member_is_denied_with_the_policy_detail(auth, method, path, action):
    r = call(auth, method, path, "u1")

    assert r.status_code == 403, r.text
    assert r.json() == {"detail": f"Policy does not allow {action}"}


@pytest.mark.parametrize("method,path,action", ROUTES)
def test_admin_is_not_denied(auth, method, path, action):
    r = call(auth, method, path, "adm")

    assert r.status_code != 403, r.text
    assert r.status_code != 401, r.text


@pytest.mark.parametrize("method,path,action", DOMAIN_ADMIN_ROUTES)
def test_domain_admin_matches_the_old_gate(auth, method, path, action):
    r = call(auth, method, path, "da")

    assert r.status_code != 403, r.text


@pytest.mark.parametrize("method,path,action", ADMIN_ONLY_ROUTES)
def test_domain_admin_is_denied_the_admin_only_actions(auth, method, path, action):
    r = call(auth, method, path, "da")

    assert r.status_code == 403, r.text
    assert r.json() == {"detail": f"Policy does not allow {action}"}


def test_the_route_table_covers_every_action_once():
    assert len(ROUTES) == len({a for _, _, a in ROUTES}) == 22
    assert len(DOMAIN_ADMIN_ROUTES) == 11 and len(ADMIN_ONLY_ROUTES) == 11


def test_every_default_action_is_used_by_a_route(auth):
    assert policy.required_actions() >= set(policy.DEFAULT_RULES)


def test_policy_file_can_open_an_action(auth, pfile):
    body = {"username": "n1", "email": "n1@x", "password": "longenoughpw1", "project_id": "p1"}
    assert auth.client.post("/users", json=body, headers=hdr(auth, "u1")).status_code == 403

    pfile.write('identity:user:create: "role:member"\n')

    r = auth.client.post("/users", json=body, headers=hdr(auth, "u1"))
    assert r.status_code == 201, r.text
    r = auth.client.delete("/users/nobody", headers=hdr(auth, "u1"))
    assert r.status_code == 403
    assert r.json() == {"detail": "Policy does not allow identity:user:delete"}


def test_policy_file_can_close_an_action_for_admins(auth, pfile):
    body = {"name": "pnew"}
    assert auth.client.post("/projects", json=body, headers=hdr(auth, "adm")).status_code == 201

    pfile.write('identity:project:create: "!"\n')

    r = auth.client.post("/projects", json={"name": "pnew2"}, headers=hdr(auth, "adm"))
    assert r.status_code == 403
    assert r.json() == {"detail": "Policy does not allow identity:project:create"}
    # the other actions keep their default
    assert auth.client.get("/users", headers=hdr(auth, "adm")).status_code == 200


def test_revoked_token_is_still_401(auth):
    headers = hdr(auth, "adm")
    assert auth.client.delete("/auth/tokens/current", headers=headers).status_code == 204

    for method, path, _ in ROUTES:
        r = auth.client.request(method, path, headers=headers)
        assert r.status_code == 401, (method, path, r.text)


def test_token_is_validated_once_per_request(auth, monkeypatch):
    headers = hdr(auth, "adm")
    calls = []
    real = auth.deps._decode_token

    def counting(token):
        calls.append(token)
        return real(token)

    monkeypatch.setattr(auth.deps, "_decode_token", counting)

    # delete_project has the gate and a `current: CurrentUser` parameter of its own
    r = auth.client.delete("/projects/nobody", headers=headers)

    assert r.status_code == 404, r.text
    assert len(calls) == 1
