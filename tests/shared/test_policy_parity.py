import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "services"))

from shared import policy  # noqa: E402

# Independent copies of services/auth/app/deps.py::_require_admin and ::_require_domain_admin.
def legacy_admin_only(t):
    return t.get("role") == "admin"


def legacy_admin_or_domain_admin(t):
    return t.get("role") == "admin" or bool(t.get("domain_admin"))


ADMIN_OR_DOMAIN_ADMIN = {
    "identity:user:create", "identity:user:list", "identity:user:delete",
    "identity:domain:list", "identity:domain:show", "identity:domain:quota-show",
    "identity:project:create", "identity:project:update", "identity:project:delete",
    "identity:role-assignment:create", "identity:role-assignment:delete",
}
ADMIN_ONLY = {
    "identity:domain:create", "identity:domain:update", "identity:domain:delete",
    "identity:domain:quota-set", "identity:domain:quota-clear",
    "identity:service:create", "identity:service:delete",
    "identity:endpoint:create", "identity:endpoint:delete",
    "identity:project:quota-set", "identity:audit-log:list",
}

IDENTITIES = {
    "system admin": {"role": "admin"},
    "domain admin": {"role": "member", "domain_admin": True},
    "member": {"role": "member"},
    "reader": {"role": "reader"},
    # what shared.auth_middleware.require_user_or_internal builds for the internal token
    "internal": {
        "sub": "internal",
        "username": "admin",
        "project_id": "admin",
        "project_name": "admin",
        "role": "admin",
        "roles": ["admin"],
        "scope_type": "system",
        "jti": "internal",
    },
}


@pytest.fixture(autouse=True)
def no_policy_file(tmp_path, monkeypatch):
    monkeypatch.setenv("CLOUDX_POLICY_FILE", str(tmp_path / "absent.yaml"))
    policy.reset()
    yield
    policy.reset()


def test_the_two_groups_cover_exactly_the_default_actions():
    assert ADMIN_OR_DOMAIN_ADMIN | ADMIN_ONLY == set(policy.DEFAULT_RULES)
    assert ADMIN_OR_DOMAIN_ADMIN & ADMIN_ONLY == set()


@pytest.mark.parametrize("identity", IDENTITIES)
def test_default_rules_decide_like_the_legacy_gate(identity):
    token = IDENTITIES[identity]
    for action in ADMIN_OR_DOMAIN_ADMIN:
        assert policy.authorize(action, token) == legacy_admin_or_domain_admin(token), (action, identity)
    for action in ADMIN_ONLY:
        assert policy.authorize(action, token) == legacy_admin_only(token), (action, identity)
