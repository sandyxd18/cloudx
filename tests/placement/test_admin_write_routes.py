"""Routes that change scheduling data are for admins and the internal caller; a member is refused."""
import importlib

import pytest
from fastapi.testclient import TestClient

from shared.auth_middleware import require_auth

ROUTES = [
    ("POST", "/resource_providers/rp-1/heartbeat", {"running_vms": 1}),
    ("PUT", "/resource_providers/rp-1/traits", {"traits": ["HW_CPU_X86_AVX"]}),
    ("PUT", "/resource_classes/CUSTOM_X", None),
    ("DELETE", "/resource_classes/CUSTOM_X", None),
    ("PUT", "/resource_providers/rp-1/aggregates", {"aggregates": ["a1"]}),
    ("POST", "/reshaper", {"inventories": {}, "allocations": {}}),
]


@pytest.fixture
def placement(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'placement.sqlite'}")
    import services.placement.app.main as main

    main = importlib.reload(main)
    main.Base.metadata.create_all(bind=main.engine)
    main.who = {"sub": "u", "role": "member", "project_id": "p1", "roles": ["member"], "scope_type": "project"}
    main.app.dependency_overrides[require_auth] = lambda: dict(main.who)
    with main.SessionLocal() as db:
        db.add(main.ResourceProvider(uuid="rp-1", name="node-1"))
        db.commit()
    return main


@pytest.mark.parametrize("method,path,body", ROUTES)
def test_a_member_is_refused(placement, method, path, body):
    r = TestClient(placement.app, raise_server_exceptions=False).request(method, path, json=body)

    assert r.status_code == 403, r.text


@pytest.mark.parametrize("method,path,body", ROUTES)
def test_an_admin_is_not_refused(placement, method, path, body):
    placement.who["role"] = "admin"

    r = TestClient(placement.app, raise_server_exceptions=False).request(method, path, json=body)

    assert r.status_code not in (401, 403), r.text
