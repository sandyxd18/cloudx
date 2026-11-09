"""Creating an external network is for admins."""
from fastapi.testclient import TestClient
from shared.auth_middleware import require_auth


def test_a_member_cannot_create_an_external_network_but_can_create_a_private_one(networking, monkeypatch):
    user = {"project_id": "p1", "role": "member"}
    networking.app.dependency_overrides[require_auth] = lambda: user
    monkeypatch.setattr(networking.ovn_cli, "ovn", lambda *a, **k: "")
    monkeypatch.setattr(networking.events, "_rpc", type("Rpc", (), {"publish_event": lambda *a, **k: None})())
    client = TestClient(networking.app)

    refused = client.post("/networks", json={"name": "ext1", "external": True})
    private = client.post("/networks", json={"name": "net1"})

    assert refused.status_code == 403
    assert private.status_code == 201
    with networking.db.SessionLocal() as db:
        assert [n.name for n in db.query(networking.models.Network).all()] == ["net1"]
