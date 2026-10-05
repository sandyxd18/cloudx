"""Deleting a network/subnet that is still in use is a 409, and must not touch OVN first.

It used to remove the OVN logical switch, then fail on the database foreign key (HTTP 500), leaving OVN and the
database out of sync.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event


@pytest.fixture
def api(networking):
    from shared.auth_middleware import require_auth

    event.listen(networking.db.engine, "connect", lambda conn, rec: conn.execute("PRAGMA foreign_keys=ON"))
    networking.app.dependency_overrides[require_auth] = lambda: {"project_id": "proj-1", "role": "admin"}
    ovn_calls = []
    networking.ovn_cli.ovn = lambda *args, **kwargs: ovn_calls.append(args) or ""
    networking.ovn_calls = ovn_calls
    networking.client = TestClient(networking.app)
    return networking


def seed(api, with_port=False):
    with api.db.SessionLocal() as db:
        db.add(api.models.Network(id="net-1", name="private"))
        db.add(api.models.Subnet(id="sub-1", name="sub", network_id="net-1", cidr="10.0.0.0/24", gateway="10.0.0.1",
                                 gateway_ip="10.0.0.1", ip_version=4))
        if with_port:
            db.add(api.models.Port(name="p1", network_id="net-1", subnet_id="sub-1", mac_address="02:00:00:00:00:01",
                                   fixed_ip="10.0.0.2"))
        db.commit()


def test_network_with_a_subnet_cannot_be_deleted(api):
    seed(api)
    res = api.client.delete("/networks/net-1")
    assert res.status_code == 409 and "subnets" in res.json()["detail"]
    assert api.ovn_calls == []                       # the logical switch is still there
    assert api.client.get("/networks/net-1").status_code == 200


def test_network_with_a_port_cannot_be_deleted(api):
    with api.db.SessionLocal() as db:
        db.add(api.models.Network(id="net-2", name="plain"))
        db.add(api.models.Port(name="p2", network_id="net-2", mac_address="02:00:00:00:00:02"))
        db.commit()
    res = api.client.delete("/networks/net-2")
    assert res.status_code == 409 and "ports" in res.json()["detail"]
    assert api.ovn_calls == []


def test_subnet_with_a_port_cannot_be_deleted(api):
    seed(api, with_port=True)
    res = api.client.delete("/subnets/sub-1")
    assert res.status_code == 409 and "ports" in res.json()["detail"]
    assert api.ovn_calls == []


def test_empty_network_and_subnet_are_deleted_as_before(api):
    seed(api)
    assert api.client.delete("/subnets/sub-1").status_code == 200
    assert api.client.delete("/networks/net-1").status_code == 200
    assert api.client.get("/networks/net-1").status_code == 404
    assert ("--if-exists", "ls-del", "private") in api.ovn_calls
