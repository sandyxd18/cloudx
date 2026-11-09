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


def test_a_member_cannot_delete_a_port_a_server_uses_but_can_delete_a_free_one(api):
    m = api.models
    with api.db.SessionLocal() as db:
        db.add_all([
            m.Port(id="p-srv", name="p-srv", network_id="n-a", project_id="proj-a", mac_address="fa:16:3e:00:00:01",
                   device_id="srv-1", device_owner="compute:instance"),
            m.Port(id="p-free", name="p-free", network_id="n-a", project_id="proj-a", mac_address="fa:16:3e:00:00:02"),
        ])
        db.commit()

    assert api.client.delete("/ports/p-srv").status_code == 409
    assert api.client.delete("/ports/p-free").status_code == 200

    api.user["role"] = "admin"
    assert api.client.delete("/ports/p-srv").status_code == 200


def test_a_subnet_of_another_network_cannot_be_named_for_a_port(api):
    m = api.models
    with api.db.SessionLocal() as db:
        db.add_all([
            m.Network(id="n-b", name="net-b", project_id="proj-b"),
            m.Subnet(id="s-b", name="sub-b", network_id="n-b", cidr="10.2.0.0/24", gateway="10.2.0.1", ip_version=4),
        ])
        db.commit()

    r = api.client.post("/ports", json={"name": "p1", "network": "net-a", "fixed_ips": [{"subnet_id": "s-b"}]})
    ok = api.client.post("/ports", json={"name": "p2", "network": "net-a", "fixed_ips": [{"subnet_id": "s-a"}]})

    assert r.status_code == 404 and r.json() == {"detail": "subnet not found"}
    assert ok.status_code == 201


def _port_without_group(api, project="proj-a", name="p-enable"):
    with api.db.SessionLocal() as db:
        db.add(api.models.Port(id=name, name=name, network_id="n-a", project_id=project, mac_address="02:00:00:00:00:09",
                               port_security_enabled=False))
        db.commit()
    return name


def test_enabling_port_security_gives_the_ports_project_default_group(api):
    with api.db.SessionLocal() as db:
        db.add_all([api.models.SecurityGroup(id="sg-a-default", name="default", project_id="proj-a"),
                    api.models.SecurityGroup(id="sg-b-default", name="default", project_id="proj-b")])
        db.commit()
    port = _port_without_group(api)
    api.user["role"] = "admin"
    api.user["project_id"] = "proj-b"

    r = api.client.post(f"/ports/{port}/security/enable")

    assert r.status_code == 200, r.text
    assert group_of(api, port) == "sg-a-default"


def test_enabling_port_security_falls_back_to_the_installation_default(api):
    with api.db.SessionLocal() as db:
        db.add(api.models.SecurityGroup(id="sg-b-default", name="default", project_id="proj-b"))
        db.commit()
    port = _port_without_group(api)
    api.user["role"] = "admin"
    api.user["project_id"] = "proj-b"

    api.client.post(f"/ports/{port}/security/enable")

    assert group_of(api, port) == "sg-shared-default"


def test_enabling_port_security_keeps_the_groups_already_set(api):
    port = _port_without_group(api)
    with api.db.SessionLocal() as db:
        db.get(api.models.Port, port).security_group_ids = "sg-shared-default"
        db.commit()

    api.client.post(f"/ports/{port}/security/enable")

    assert group_of(api, port) == "sg-shared-default"
