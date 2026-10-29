from tests.compute import test_catalog_failures

api, _server, _status = test_catalog_failures.api, test_catalog_failures._server, test_catalog_failures._status


def test_unsuspend_restores_a_suspended_server(api):
    # the worker had an unsuspend handler but compute-api had no route to reach it
    svc, client, casts, _ = api
    _server(svc, "SUSPENDED")
    r = client.post("/servers/srv1/unsuspend")
    assert r.status_code == 200 and r.json()["status"] == "RESUMING"
    assert casts == ["unsuspend"]
    assert _status(svc) == "RESUMING"


def test_unsuspend_refuses_a_server_that_is_not_suspended(api):
    svc, client, casts, _ = api
    for status in ("ACTIVE", "PAUSED", "SHUTOFF"):
        with svc.SessionLocal() as db:
            db.query(svc.Server).delete()
            db.commit()
        _server(svc, status)
        r = client.post("/servers/srv1/unsuspend")
        assert r.status_code == 409 and "SUSPENDED" in r.json()["detail"]
        assert _status(svc) == status
    assert casts == []


def test_unsuspend_of_an_unknown_server_is_404(api):
    _, client, casts, _ = api
    assert client.post("/servers/nope/unsuspend").status_code == 404
    assert casts == []
