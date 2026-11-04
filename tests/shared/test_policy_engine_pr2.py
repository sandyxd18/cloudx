"""PR 2 of the policy file: the 35 admin actions of the other services, check() and the .action marker."""
import re

import pytest
from fastapi import Depends, FastAPI

from shared import policy
from tests.gates import route_actions

NEW_ACTIONS = {
    "compute:flavor:create", "compute:flavor:delete",
    "compute:flavor-extra-spec:set", "compute:flavor-extra-spec:delete",
    "compute:aggregate:create", "compute:aggregate:update", "compute:aggregate:delete", "compute:aggregate:action",
    "compute:quota:set", "compute:service:update", "compute:server:migrate", "compute:server:evacuate",
    "block-store:volume-type:create", "block-store:volume-type:update", "block-store:volume-type:delete",
    "block-store:quota:set", "block-store:quota:clear",
    "block-store:volume:force-detach", "block-store:volume:force-delete", "block-store:volume:reset-status",
    "placement:resource-provider:create", "placement:resource-provider:delete",
    "placement:inventory:set", "placement:allocation:create", "placement:allocation:delete",
    "billing:rating-sheet:create", "billing:rating-sheet:update", "billing:usage-record:create",
    "networking:qos-policy:delete", "networking:subnet-pool:delete",
    "object-store:bucket-type:create", "object-store:bucket-type:delete",
    "telemetry:metric:create", "telemetry:alarm:evaluate",
    "ha:host:reset",
}

ADMIN = {"role": "admin"}
MEMBER = {"role": "member"}


def test_the_table_has_57_actions_35_of_them_new_and_role_admin():
    assert len(NEW_ACTIONS) == 35
    assert len(policy.DEFAULT_RULES) == 57
    for action in NEW_ACTIONS:
        assert policy.DEFAULT_RULES[action] == "role:admin", action
    new = set(policy.DEFAULT_RULES) - {a for a in policy.DEFAULT_RULES if a.startswith("identity:")}
    assert new == NEW_ACTIONS
    assert len([a for a in policy.DEFAULT_RULES if a.startswith("identity:")]) == 22
    assert not any(a.startswith("identity:") for a in NEW_ACTIONS)
    for action in policy.DEFAULT_RULES:
        assert re.fullmatch(r"[a-z-]+:[a-z-]+:[a-z-]+", action), action


def test_the_table_is_read_only():
    with pytest.raises(TypeError):
        policy.DEFAULT_RULES["compute:flavor:create"] = "@"
    with pytest.raises(TypeError):
        policy.DEFAULT_RULES["compute:new:thing"] = "@"


def test_check_allows_an_admin_and_refuses_a_member_with_the_policy_text():
    assert policy.check("compute:flavor:create", ADMIN) is None
    with pytest.raises(policy.HTTPException) as exc:
        policy.check("compute:flavor:create", MEMBER)
    assert exc.value.status_code == 403
    assert exc.value.detail == "Policy does not allow compute:flavor:create"


def test_check_raises_keyerror_for_an_unknown_action():
    with pytest.raises(KeyError):
        policy.check("compute:flavor:explode", ADMIN)


def test_check_follows_the_policy_file(tmp_path, monkeypatch):
    path = tmp_path / "policy.yaml"
    path.write_text('"compute:flavor:create": "role:member"\n', encoding="utf-8")
    monkeypatch.setenv("CLOUDX_POLICY_FILE", str(path))
    policy.reset()

    assert policy.check("compute:flavor:create", MEMBER) is None
    with pytest.raises(policy.HTTPException):
        policy.check("compute:flavor:delete", MEMBER)


def test_require_marks_its_dependency_with_the_action():
    dependency = policy.require("compute:flavor:create", authenticate=lambda: ADMIN)

    assert dependency.action == "compute:flavor:create"


def test_route_actions_reads_the_action_of_a_route():
    app = FastAPI()
    gate = policy.require("compute:flavor:create", authenticate=lambda: ADMIN)

    @app.post("/flavors")
    def create(_: dict = Depends(gate)):
        return {}

    @app.get("/open")
    def open_route():
        return {}

    assert route_actions(app) == {("POST", "/flavors"): "compute:flavor:create"}


def test_route_actions_sees_a_gate_inside_a_nested_dependency():
    app = FastAPI()
    gate = policy.require("ha:host:reset", authenticate=lambda: ADMIN)

    def inner(token: dict = Depends(gate)):
        return token

    @app.delete("/hosts/{name}")
    def reset_host(name: str, _: dict = Depends(inner)):
        return {}

    assert route_actions(app) == {("DELETE", "/hosts/{name}"): "ha:host:reset"}


def test_a_leftover_cache_or_a_real_policy_file_does_not_leak_into_the_next_test(tmp_path, monkeypatch):
    closed = tmp_path / "closing.yaml"
    closed.write_text('"compute:flavor:create": "!"\n', encoding="utf-8")
    monkeypatch.setenv("CLOUDX_POLICY_FILE", str(closed))
    policy.reset()

    assert policy.authorize("compute:flavor:create", ADMIN) is False
    assert policy.sources()["compute:flavor:create"] == "file"


def test_a_leftover_cache_or_a_real_policy_file_does_not_leak_into_the_next_test_part_two():
    import os

    assert "file" not in policy.sources().values()
    assert not os.path.exists(os.environ["CLOUDX_POLICY_FILE"])
    assert policy.authorize("compute:flavor:create", ADMIN) is True
