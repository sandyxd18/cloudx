"""resize_pending_since is non-NULL only while a server is in VERIFY_RESIZE. confirmResize and revertResize write the
status straight to the compute DB (not through the conductor), so they have to clear the timestamp themselves."""
from datetime import datetime

from tests.compute import test_catalog_failures

api, _server = test_catalog_failures.api, test_catalog_failures._server


def _waiting(svc):
    _server(svc, "VERIFY_RESIZE", flavor_name="big", vcpus=4, ram_mb=2048, original_flavor="small", original_vcpus=1,
            original_ram_mb=512, resize_pending_since=datetime(2026, 1, 1))


def _record(svc):
    with svc.SessionLocal() as db:
        s = db.get(svc.Server, "srv1")
        return s.status, s.resize_pending_since


def test_confirm_resize_clears_the_pending_timestamp(api):
    svc, client, _, _ = api
    _waiting(svc)

    assert client.post("/servers/srv1/confirmResize").status_code == 200

    assert _record(svc) == ("ACTIVE", None)


def test_revert_resize_clears_the_pending_timestamp(api):
    svc, client, _, _ = api
    _waiting(svc)

    assert client.post("/servers/srv1/revertResize").status_code == 200

    assert _record(svc) == ("RESIZE", None)
