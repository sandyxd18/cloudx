import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from jose import jwt

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "services"))

from shared import policy  # noqa: E402

SERVICES = os.path.join(os.path.dirname(__file__), "..", "..", "services")
ACTION = "identity:user:create"


def fake_auth():
    """A stand-in for the token dependency: the payload comes from a request header."""
    from fastapi import Request

    def authenticate(request: Request) -> dict:
        who = request.headers.get("X-Who")
        if who is None:
            raise HTTPException(status_code=401, detail="authentication required")
        return {"role": who, "sub": "u1"}

    return authenticate


def make_app(action=ACTION):
    app = FastAPI()

    @app.get("/x")
    def x(token: dict = Depends(policy.require(action, fake_auth()))):
        return token

    return app


@pytest.fixture(autouse=True)
def isolated_policy(tmp_path, monkeypatch):
    monkeypatch.setenv("CLOUDX_POLICY_FILE", str(tmp_path / "policy.yaml"))
    policy.reset()
    yield
    policy.reset()


def test_allowed_returns_the_payload():
    r = TestClient(make_app()).get("/x", headers={"X-Who": "admin"})
    assert r.status_code == 200
    assert r.json() == {"role": "admin", "sub": "u1"}


def test_denied_answers_403_with_the_action_in_the_detail():
    r = TestClient(make_app()).get("/x", headers={"X-Who": "member"})
    assert r.status_code == 403
    assert r.json() == {"detail": "Policy does not allow identity:user:create"}


def test_authentication_error_is_not_hidden():
    r = TestClient(make_app()).get("/x")
    assert r.status_code == 401
    assert r.json() == {"detail": "authentication required"}


def test_unknown_action_raises_key_error_immediately():
    with pytest.raises(KeyError):
        policy.require("identity:nothing:here", fake_auth())
    assert "identity:nothing:here" not in policy.required_actions()


def test_required_actions_records_the_actions():
    policy.require("identity:domain:list", fake_auth())
    policy.require("identity:audit-log:list", fake_auth())
    seen = policy.required_actions()
    assert {"identity:domain:list", "identity:audit-log:list"} <= seen
    seen.add("bogus")  # a copy: the module's own record is untouched
    assert "bogus" not in policy.required_actions()


def test_custom_authenticate_does_not_import_the_auth_middleware():
    code = (
        "import sys; sys.path.insert(0, %r)\n"
        "from shared import policy\n"
        "assert 'shared.auth_middleware' not in sys.modules\n"
        "policy.require('identity:user:list', lambda: {})\n"
        "assert 'shared.auth_middleware' not in sys.modules\n"
    ) % SERVICES
    env = {k: v for k, v in os.environ.items() if k != "AUTH_SECRET_KEY"}
    done = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_policy_imports_without_auth_secret_key():
    code = "import sys; sys.path.insert(0, %r)\nimport shared.policy\n" % SERVICES
    env = {k: v for k, v in os.environ.items() if k != "AUTH_SECRET_KEY"}
    done = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_default_authenticate_is_the_shared_require_auth(monkeypatch):
    monkeypatch.setenv("AUTH_SECRET_KEY", "test-secret-key-not-for-production-32b")
    monkeypatch.setenv("REVOCATION_CHECK", "false")
    app = FastAPI()

    @app.get("/x", dependencies=[Depends(policy.require(ACTION))])
    def x():
        return {"ok": True}

    from shared import auth_middleware as am

    def headers(role):
        claims = {"sub": "u1", "username": "u1", "project_id": "p1", "role": role, "roles": [role],
                  "exp": datetime.now(timezone.utc) + timedelta(hours=1)}
        return {"X-Auth-Token": jwt.encode(claims, am.SECRET_KEY, algorithm=am.ALGORITHM)}

    c = TestClient(app)
    assert c.get("/x", headers=headers("admin")).status_code == 200
    assert c.get("/x", headers=headers("member")).status_code == 403
    assert c.get("/x").status_code == 401


def test_file_change_applies_to_the_next_request(tmp_path, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(policy, "_monotonic", lambda: now[0])
    app = make_app()
    c = TestClient(app)
    assert c.get("/x", headers={"X-Who": "member"}).status_code == 403

    path = tmp_path / "policy.yaml"
    path.write_text(f'{ACTION}: "role:member"\n', encoding="utf-8")
    os.utime(path, ns=(10**18, 10**18))
    now[0] += policy.CHECK_INTERVAL + 1
    assert c.get("/x", headers={"X-Who": "member"}).status_code == 200
