"""The 5 admin-only routes of placement are gated by the central policy; the /internal door is not."""
import importlib

import pytest
from fastapi.testclient import TestClient

from shared.auth_middleware import require_auth, require_internal
from tests.gates import assert_gated, route_actions

EXPECTED = {
    ("POST", "/resource_providers"): "placement:resource-provider:create",
    ("DELETE", "/resource_providers/{rp_uuid}"): "placement:resource-provider:delete",
    ("PUT", "/resource_providers/{rp_uuid}/inventories"): "placement:inventory:set",
    ("POST", "/allocations"): "placement:allocation:create",
    ("DELETE", "/allocations/{consumer_uuid}"): "placement:allocation:delete",
}
ROUTES = [(method, path, action) for (method, path), action in EXPECTED.items()]
INVENTORY = {"total": 4, "reserved": 0, "min_unit": 1, "max_unit": 4, "step_size": 1, "allocation_ratio": 1.0}


@pytest.fixture
def placement(monkeypatch, tmp_path, pfile):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'placement.sqlite'}")
    import services.placement.app.main as main

    main = importlib.reload(main)
    main.Base.metadata.create_all(bind=main.engine)
    who = {"sub": "u", "role": "admin", "project_id": "p1", "domain_admin": False}
    main.app.dependency_overrides[require_auth] = lambda: who
    main.app.dependency_overrides[require_internal] = lambda: None
    main.who = who
    with main.SessionLocal() as db:
        db.add(main.ResourceProvider(uuid="rp-1", name="node-1"))
        db.commit()
    return main


def test_each_gated_route_has_its_action(placement):
    found = {key: action for key, action in route_actions(placement.app).items() if action.startswith("placement:")}

    assert found == EXPECTED
    assert len(found) == 5
    assert not [path for (_, path) in found if path.startswith("/internal")]


def test_member_domain_admin_and_reader_are_refused_and_admin_is_not(placement):
    assert_gated(placement.app, placement.who, ROUTES)


def test_admin_runs_the_gated_routes_without_a_server_error(placement):
    client = TestClient(placement.app, raise_server_exceptions=False)
    calls = [
        ("POST", "/resource_providers", {"name": "node-2"}, 201),
        ("PUT", "/resource_providers/rp-1/inventories", {"inventories": {"VCPU": INVENTORY}}, 200),
        ("POST", "/allocations", {"consumer_uuid": "vm-1", "project_id": "p1",
                                  "allocations": {"rp-1": {"resources": {"VCPU": 2}}}}, 201),
        ("DELETE", "/allocations/vm-1", None, 204),
        ("DELETE", "/resource_providers/rp-1", None, 204),
    ]
    for method, path, body, expected in calls:
        r = client.request(method, path, json=body)
        assert r.status_code == expected, (method, path, r.status_code, r.text)


def test_set_inventories_still_takes_none_in_the_admin_slot(placement):
    with placement.SessionLocal() as db:
        out = placement.set_inventories("rp-1", placement.InventoriesIn(inventories={"VCPU": INVENTORY}), None, db)

    assert list(out["inventories"]) == ["VCPU"]


def test_set_inventories_internal_still_works_through_the_internal_door(placement):
    client = TestClient(placement.app, raise_server_exceptions=False)

    r = client.put("/internal/resource_providers/node-1/inventories", json={"inventories": {"VCPU": INVENTORY}})

    assert r.status_code == 200, r.text
    with placement.SessionLocal() as db:
        assert db.query(placement.Inventory).filter_by(resource_provider_id="rp-1", resource_class="VCPU").count() == 1


def test_the_internal_door_is_not_governed_by_the_file(placement, pfile):
    pfile.write('"placement:inventory:set": "!"\n"placement:resource-provider:create": "!"\n')
    client = TestClient(placement.app, raise_server_exceptions=False)

    closed = client.put("/resource_providers/rp-1/inventories", json={"inventories": {"VCPU": INVENTORY}})
    assert closed.status_code == 403
    assert closed.json() == {"detail": "Policy does not allow placement:inventory:set"}

    r = client.put("/internal/resource_providers/node-1/inventories", json={"inventories": {"VCPU": INVENTORY}})
    assert r.status_code == 200, r.text
    r = client.post("/internal/resource_providers", json={"name": "node-3"})
    assert r.status_code == 201, r.text


def test_a_policy_file_can_close_resource_provider_delete_for_admins(placement, pfile):
    client = TestClient(placement.app, raise_server_exceptions=False)
    make = client.post("/resource_providers", json={"name": "node-9"})
    assert make.status_code == 201, make.text
    target = make.json()["uuid"]

    pfile.write('"placement:resource-provider:delete": "!"\n')

    r = client.delete(f"/resource_providers/{target}")
    assert r.status_code == 403
    assert r.json() == {"detail": "Policy does not allow placement:resource-provider:delete"}
    assert client.get(f"/resource_providers/{target}").status_code == 200
