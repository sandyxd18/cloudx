"""metadata-lookup by address answers only when exactly one live server has that address."""
from tests.compute import test_catalog_failures

api = test_catalog_failures.api


def _add(svc, sid, ip, status="ACTIVE", project="p1"):
    with svc.SessionLocal() as db:
        db.add(svc.Server(id=sid, name=sid, project_id=project, image_id="img1", root_volume_id="vol-" + sid,
                          flavor="small", network_id="net-" + sid, port_id="port-" + sid, host="node-1",
                          status=status, ip_address=ip))
        db.commit()


def test_one_server_with_the_address_is_found(api):
    svc, client, _, _ = api
    _add(svc, "s1", "10.0.0.5")

    r = client.get("/servers/metadata-lookup", params={"fixed_ip": "10.0.0.5"})

    assert r.status_code == 200


def test_two_live_servers_with_the_same_address_are_not_answered(api):
    svc, client, _, _ = api
    _add(svc, "s1", "10.0.0.5", project="p1")
    _add(svc, "s2", "10.0.0.5", project="p2")

    r = client.get("/servers/metadata-lookup", params={"fixed_ip": "10.0.0.5"})

    assert r.status_code == 404


def test_a_deleted_server_does_not_make_the_address_ambiguous(api):
    svc, client, _, _ = api
    _add(svc, "s1", "10.0.0.5")
    _add(svc, "s2", "10.0.0.5", status="DELETED")

    assert client.get("/servers/metadata-lookup", params={"fixed_ip": "10.0.0.5"}).status_code == 200


def test_lookup_by_instance_id_is_unchanged_when_addresses_overlap(api):
    svc, client, _, _ = api
    _add(svc, "s1", "10.0.0.5", project="p1")
    _add(svc, "s2", "10.0.0.5", project="p2")

    assert client.get("/servers/metadata-lookup", params={"instance_id": "s2"}).status_code == 200
