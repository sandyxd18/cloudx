"""Adding a bandwidth rule to a QoS policy answered 500 (`type object 'Port' has no attribute 'qos_policy_id'`) after it had
already stored the rule, so the dashboard showed an error for a rule that existed and a retry made a second one."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def api(networking):
    from shared.auth_middleware import require_auth

    networking.app.dependency_overrides[require_auth] = lambda: {"project_id": "proj-a", "role": "admin"}
    networking.client = TestClient(networking.app)
    return networking


def test_a_bandwidth_rule_is_added_and_answers_201(api):
    policy = api.client.post("/qos/policies", json={"name": "q1"}).json()

    res = api.client.post(f"/qos/policies/{policy['id']}/bandwidth_limit_rules",
                          json={"max_kbps": 2000, "direction": "egress"})

    assert res.status_code == 201, res.text
    assert res.json()["max_kbps"] == 2000 and res.json()["direction"] == "egress"


def test_the_rule_is_stored_once_and_shown_with_the_policy(api):
    policy = api.client.post("/qos/policies", json={"name": "q2"}).json()
    api.client.post(f"/qos/policies/{policy['id']}/bandwidth_limit_rules", json={"max_kbps": 500, "direction": "ingress"})

    shown = api.client.get(f"/qos/policies/{policy['id']}").json()

    assert [(r["max_kbps"], r["direction"]) for r in shown["rules"]] == [(500, "ingress")]


def test_a_rule_can_be_deleted(api):
    policy = api.client.post("/qos/policies", json={"name": "q3"}).json()
    rule = api.client.post(f"/qos/policies/{policy['id']}/bandwidth_limit_rules", json={"max_kbps": 100}).json()

    assert api.client.delete(f"/qos/policies/{policy['id']}/bandwidth_limit_rules/{rule['id']}").status_code == 204
    assert api.client.get(f"/qos/policies/{policy['id']}").json()["rules"] == []
