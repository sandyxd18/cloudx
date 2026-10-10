import importlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event


@pytest.fixture
def placement(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'placement.sqlite'}")
    import services.placement.app.main as main

    main = importlib.reload(main)
    # sqlite ignores foreign keys unless asked; PostgreSQL (production) enforces them
    event.listen(main.engine, "connect", lambda conn, rec: conn.execute("PRAGMA foreign_keys=ON"))
    main.Base.metadata.create_all(bind=main.engine)

    from shared.auth_middleware import require_auth

    main.app.dependency_overrides[require_auth] = lambda: {"project_id": "proj-1", "role": "admin"}
    main.client = TestClient(main.app)
    return main


def make_provider(main, name, **extra):
    with main.SessionLocal() as db:
        rp = main.ResourceProvider(name=name, **extra)
        db.add(rp)
        db.commit()
        return rp.uuid


def add_children(main, uuid):
    with main.SessionLocal() as db:
        db.add(main.Inventory(resource_provider_id=uuid, resource_class="VCPU", total=8))
        db.add(main.ComputeNodeHeartbeat(resource_provider_id=uuid, host="node-1"))
        db.commit()


def count(main, model):
    with main.SessionLocal() as db:
        return db.query(model).count()


def test_deleting_a_provider_removes_its_inventories_and_heartbeat(placement):
    uuid = make_provider(placement, "node-1")
    add_children(placement, uuid)
    other = make_provider(placement, "node-2")
    add_children(placement, other)

    assert placement.client.delete(f"/resource_providers/{uuid}").status_code == 204   # used to be a 500 (FK violation)

    assert placement.client.get(f"/resource_providers/{uuid}").status_code == 404
    assert count(placement, placement.Inventory) == 1 and count(placement, placement.ComputeNodeHeartbeat) == 1  # node-2's


def test_a_provider_with_allocations_cannot_be_deleted(placement):
    uuid = make_provider(placement, "node-1")
    with placement.SessionLocal() as db:
        db.add(placement.Allocation(consumer_uuid="vm-1", project_id="p", resource_provider_id=uuid, resource_class="VCPU", used=2))
        db.commit()
    res = placement.client.delete(f"/resource_providers/{uuid}")
    assert res.status_code == 409 and "allocations" in res.json()["detail"]
    assert placement.client.get(f"/resource_providers/{uuid}").status_code == 200


def test_a_provider_with_children_cannot_be_deleted(placement):
    parent = make_provider(placement, "numa-root")
    make_provider(placement, "numa-0", parent_provider_id=parent)
    assert placement.client.delete(f"/resource_providers/{parent}").status_code == 409


def test_deleting_an_unknown_provider_is_a_404(placement):
    assert placement.client.delete("/resource_providers/nope").status_code == 404


def test_put_inventories_twice_replaces(placement):
    uuid = make_provider(placement, "h1")
    item = {"total": 4, "reserved": 0, "min_unit": 1, "max_unit": 4, "step_size": 1, "allocation_ratio": 1.0}
    with placement.SessionLocal() as db:
        for total in (4, 8):
            payload = placement.InventoriesIn(inventories={"VCPU": dict(item, total=total)})
            placement.set_inventories(uuid, payload, None, db)
        assert [i.total for i in db.query(placement.Inventory)] == [8]