import importlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from shared import catalog

# Every API that resolves a peer through the catalog in a request path. compute already answered 502.
SERVICES = ["compute", "block_store", "orchestration", "load_balancer", "database"]
REASON = "cannot resolve service 'image': auth is unreachable"


@pytest.mark.parametrize("svc", SERVICES)
def test_an_unresolvable_peer_is_a_502_with_the_reason_not_a_500(svc, monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / (svc + '.sqlite')}")
    real = importlib.import_module(f"services.{svc}.app.main").app

    # A probe app that carries only the service's exception handlers, so the service's own app stays untouched.
    probe = FastAPI()
    probe.exception_handlers.update(real.exception_handlers)

    def catalog_down():
        raise catalog.CatalogError(REASON)

    probe.add_api_route("/__catalog_down", catalog_down)
    r = TestClient(probe, raise_server_exceptions=False).get("/__catalog_down")
    assert r.status_code == 502
    assert r.json() == {"detail": REASON}
