"""Live migration is not built yet. The worker reads `server_id` where the API sends `id`, so the request used to leave
the server in MIGRATING for good. Until it is built the API refuses the request and touches nothing."""
from tests.compute import test_catalog_failures

api, _server, _status = test_catalog_failures.api, test_catalog_failures._server, test_catalog_failures._status


def test_live_migrate_is_refused_as_not_implemented_and_the_server_keeps_its_status(api):
    svc, client, casts, _ = api
    _server(svc, "ACTIVE")

    r = client.post("/servers/srv1/live-migrate", json={"target_host": "node-2"})

    assert r.status_code == 501
    assert "migrate" in r.json()["detail"] and "not supported" in r.json()["detail"]
    assert _status(svc) == "ACTIVE"
    assert casts == []
