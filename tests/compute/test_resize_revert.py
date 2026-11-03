"""A server record names its flavor (servers.py stores flavor.name); resize stored the flavor id instead, so
revertResize looked the old flavor up by a name that is not its key, found nothing and left the new flavor's name on
the server. The revert also set ACTIVE before the worker had put the old size back."""
from tests.compute import test_catalog_failures

api, _server, _status = test_catalog_failures.api, test_catalog_failures._server, test_catalog_failures._status


def _add_big(svc):
    with svc.SessionLocal() as db:
        db.add(svc.Flavor(name="big", vcpus=4, ram_mb=2048, disk_gb=1))
        db.commit()


def _record(svc):
    with svc.SessionLocal() as db:
        s = db.get(svc.Server, "srv1")
        return s.flavor, s.flavor_name, s.vcpus, s.ram_mb, s.status


def test_resize_keeps_the_flavor_field_as_a_name_like_create_does(api):
    svc, client, casts, _ = api
    _add_big(svc)
    _server(svc, "ACTIVE", flavor_name="small", vcpus=1, ram_mb=512)

    assert client.post("/servers/srv1/resize", json={"flavor": "big"}).status_code == 200

    assert _record(svc) == ("big", "big", 4, 2048, "RESIZE")
    assert casts == ["resize"]


def test_revert_restores_the_name_vcpus_and_ram_of_the_old_flavor(api):
    svc, client, casts, _ = api
    _add_big(svc)
    _server(svc, "ACTIVE", flavor_name="small", vcpus=1, ram_mb=512)
    client.post("/servers/srv1/resize", json={"flavor": "big"})

    r = client.post("/servers/srv1/revertResize")

    assert r.status_code == 200
    assert _record(svc)[:4] == ("small", "small", 1, 512)
    assert casts == ["resize", "resize"]


def test_revert_leaves_the_status_to_the_worker(api):
    svc, client, _, _ = api
    _add_big(svc)
    _server(svc, "ACTIVE", flavor_name="small", vcpus=1, ram_mb=512)
    client.post("/servers/srv1/resize", json={"flavor": "big"})

    client.post("/servers/srv1/revertResize")

    assert _status(svc) == "RESIZE", "the worker sets ACTIVE once the old size is back"


def test_revert_of_a_server_that_was_not_resized_is_409(api):
    svc, client, casts, _ = api
    _server(svc, "ACTIVE", flavor_name="small", vcpus=1, ram_mb=512)

    assert client.post("/servers/srv1/revertResize").status_code == 409
    assert casts == []
