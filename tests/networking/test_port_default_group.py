"""A port created without naming a security group gets the "default" group, for a member as for an admin."""
import pytest
from fastapi.testclient import TestClient
from shared.auth_middleware import require_auth


@pytest.fixture
def api(networking, monkeypatch):
    user = {"project_id": "proj-a", "role": "member"}
    networking.user = user
    networking.app.dependency_overrides[require_auth] = lambda: user
    monkeypatch.setattr(networking.ovn_cli, "ovn", lambda *args, **kwargs: "")
    monkeypatch.setattr(networking.events, "_rpc", type("Rpc", (), {"publish_event": lambda *a, **k: None})())
    m = networking.models
    with networking.db.SessionLocal() as db:
        db.add_all([
            m.Network(id="n-a", name="net-a", project_id="proj-a"),
            m.Subnet(id="s-a", name="sub-a", network_id="n-a", cidr="10.1.0.0/24", gateway="10.1.0.1", ip_version=4),
            m.SecurityGroup(id="sg-shared-default", name="default", project_id="default"),
            m.SecurityGroup(id="sg-b", name="group-b", project_id="proj-b"),
        ])
        db.commit()
    networking.client = TestClient(networking.app)
    return networking


def group_of(api, port_id):
    with api.db.SessionLocal() as db:
        return db.get(api.models.Port, port_id).security_group_ids


def test_a_member_without_a_named_group_gets_the_default_group(api):
    r = api.client.post("/ports", json={"name": "p1", "network": "net-a"})

    assert r.status_code == 201
    assert group_of(api, r.json()["id"]) == "sg-shared-default"


def test_a_project_with_a_default_group_of_its_own_gets_that_one(api):
    with api.db.SessionLocal() as db:
        db.add(api.models.SecurityGroup(id="sg-own-default", name="default", project_id="proj-a"))
        db.commit()

    r = api.client.post("/ports", json={"name": "p1", "network": "net-a"})

    assert r.status_code == 201
    assert group_of(api, r.json()["id"]) == "sg-own-default"


def test_a_group_of_another_project_is_still_refused(api):
    r = api.client.post("/ports", json={"name": "p1", "network": "net-a", "security_group_ids": ["sg-b"]})

    assert r.status_code == 404 and r.json() == {"detail": "security-group not found"}


def test_an_admin_without_a_named_group_gets_the_default_group(api):
    api.user["role"] = "admin"

    r = api.client.post("/ports", json={"name": "p1", "network": "net-a"})

    assert r.status_code == 201
    assert group_of(api, r.json()["id"]) == "sg-shared-default"
