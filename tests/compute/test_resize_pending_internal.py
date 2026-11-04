"""GET /internal/resize-pending: the reaper's list of servers left in VERIFY_RESIZE for longer than a window."""
from datetime import timedelta

from tests.compute import test_catalog_failures

from services.compute.app.models import now as svc_now

api = test_catalog_failures.api


def _add(svc, sid, status, since):
    with svc.SessionLocal() as db:
        db.add(svc.Server(id=sid, name=sid, project_id="p1", image_id="img1", root_volume_id="vol-" + sid, flavor="small",
                          network_id="net1", port_id="port-" + sid, host="node-1", status=status, resize_pending_since=since))
        db.commit()


def _ago(**kw):
    return svc_now() - timedelta(**kw)


def test_returns_only_servers_past_the_window(api):
    svc, client, _, _ = api
    _add(svc, "old", "VERIFY_RESIZE", _ago(hours=30))
    _add(svc, "fresh", "VERIFY_RESIZE", _ago(hours=1))

    r = client.get("/internal/resize-pending", params={"seconds": 86400})

    assert r.status_code == 200 and r.json() == {"items": [{"id": "old"}]}


def test_excludes_null_timestamp(api):
    svc, client, _, _ = api
    _add(svc, "legacy", "VERIFY_RESIZE", None)

    assert client.get("/internal/resize-pending", params={"seconds": 0}).json() == {"items": []}


def test_excludes_a_stale_timestamp_on_a_server_that_is_not_in_verify_resize(api):
    svc, client, _, _ = api
    _add(svc, "active", "ACTIVE", _ago(days=3))

    assert client.get("/internal/resize-pending", params={"seconds": 60}).json() == {"items": []}


def test_requires_internal_auth(api, monkeypatch):
    svc, client, _, _ = api
    _add(svc, "old", "VERIFY_RESIZE", _ago(hours=30))
    monkeypatch.setenv("INTERNAL_TOKEN", "tok")

    assert client.get("/internal/resize-pending", params={"seconds": 60}).status_code in (401, 403)
    r = client.get("/internal/resize-pending", params={"seconds": 60}, headers={"X-Internal-Token": "tok"})
    assert r.status_code == 200 and r.json() == {"items": [{"id": "old"}]}
