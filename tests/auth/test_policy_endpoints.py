"""GET /policy and POST /policy/enforce answer from the central policy engine (shared.policy)."""
import pytest

from shared import policy  # noqa: E402
from tests.auth.test_policy_routes import auth, hdr, pfile  # noqa: F401,E402


def test_get_policy_lists_effective_rules_and_sources(auth, pfile):  # noqa: F811
    r = auth.client.get("/policy", headers=hdr(auth, "u1"))

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["policy"] == policy.DEFAULT_RULES
    assert len(body["policy"]) == 22
    assert body["sources"] == {action: "default" for action in policy.DEFAULT_RULES}

    pfile.write('identity:user:create: "role:member"\n')

    body = auth.client.get("/policy", headers=hdr(auth, "u1")).json()
    assert body["policy"]["identity:user:create"] == "role:member"
    assert body["policy"]["identity:user:list"] == policy.DEFAULT_RULES["identity:user:list"]
    assert {a for a, s in body["sources"].items() if s == "file"} == {"identity:user:create"}


def test_enforce_uses_the_engine(auth, pfile):  # noqa: F811
    def ask(username):
        r = auth.client.post("/policy/enforce", json={"action": "identity:user:create"}, headers=hdr(auth, username))
        assert r.status_code == 200, r.text
        return r.json()

    assert ask("u1") == {"action": "identity:user:create", "allowed": False, "role": "member"}
    assert ask("adm") == {"action": "identity:user:create", "allowed": True, "role": "admin"}

    pfile.write('identity:user:create: "role:member"\n')

    assert ask("u1")["allowed"] is True


def test_enforce_ignores_target_project_id(auth):  # noqa: F811
    r = auth.client.post("/policy/enforce", json={"action": "identity:user:create", "target_project_id": "p1"},
                         headers=hdr(auth, "adm"))

    assert r.status_code == 200, r.text
    assert r.json()["allowed"] is True


@pytest.mark.parametrize("body", [{}, {"action": ""}, {"action": None}])
def test_enforce_without_action_is_400(auth, body):  # noqa: F811
    r = auth.client.post("/policy/enforce", json=body, headers=hdr(auth, "adm"))

    assert r.status_code == 400
    assert r.json() == {"detail": "'action' is required"}


@pytest.mark.parametrize("action", ["identity:nothing:here", "identity:create_project", "a'b\n", 5, ["identity:user:create"]])
def test_enforce_unknown_action_is_400(auth, action):  # noqa: F811
    r = auth.client.post("/policy/enforce", json={"action": action}, headers=hdr(auth, "adm"))

    assert r.status_code == 400
    assert r.json() == {"detail": f"unknown action {action!r}"}


def test_enforce_requires_a_token(auth):  # noqa: F811
    r = auth.client.post("/policy/enforce", json={"action": "identity:user:create"})

    assert r.status_code == 401


def test_get_policy_requires_a_token(auth):  # noqa: F811
    assert auth.client.get("/policy").status_code == 401
