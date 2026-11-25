"""While a server is MIGRATING its domain may run on two nodes and the source worker drives it: lifecycle actions are
refused with 409. The escape hatches are the finish hook (which settles host/status even when the worker's conductor
cast was lost), worker-start recovery, and evacuate of a MIGRATING server whose source node is forced down."""
import pytest

from tests.compute.test_catalog_failures import _status, api  # noqa: F401 (fixture)
from tests.compute.test_live_migrate import _host, _server, _service, internal, wired  # noqa: F401 (fixtures)

ROUTES = [
    ("start", "post", "/servers/srv1/start", None),
    ("stop", "post", "/servers/srv1/stop", None),
    ("reboot", "post", "/servers/srv1/reboot", {"type": "HARD"}),
    ("pause", "post", "/servers/srv1/pause", None),
    ("resume", "post", "/servers/srv1/resume", None),
    ("suspend", "post", "/servers/srv1/suspend", None),
    ("rebuild", "post", "/servers/srv1/rebuild", {"image": "img1"}),
    ("rescue", "post", "/servers/srv1/rescue", {}),
    ("unrescue", "post", "/servers/srv1/unrescue", None),
    ("password", "post", "/servers/srv1/password", {"password": "s3cret-Pass"}),
    ("delete", "delete", "/servers/srv1", None),
    ("port detach", "delete", "/servers/srv1/ports/port2", None),
]


@pytest.mark.parametrize("method,path,body", [r[1:] for r in ROUTES], ids=[r[0] for r in ROUTES])
def test_a_migrating_server_refuses_lifecycle_actions(wired, method, path, body):
    svc, client, state, _ = wired
    _server(svc, "MIGRATING", extra_port_ids="port2")

    r = client.request(method.upper(), path, json=body)

    assert r.status_code == 409, r.text
    assert "MIGRATING" in r.json()["detail"]
    assert state["casts"] == []
    assert _status(svc) == "MIGRATING"


@pytest.mark.parametrize("method,path,body", [r[1:] for r in ROUTES[:6]], ids=[r[0] for r in ROUTES[:6]])
def test_the_guard_does_not_touch_other_statuses(wired, method, path, body):
    svc, client, state, _ = wired
    _server(svc, "ACTIVE")

    r = client.request(method.upper(), path, json=body)

    assert r.status_code == 200, r.text
    assert len(state["casts"]) == 1


def test_evacuate_takes_a_migrating_server_off_a_forced_down_source(wired, monkeypatch):
    svc, client, state, _ = wired
    monkeypatch.setattr(svc.clients, "release_server_volumes", lambda server_id: None)
    _server(svc, "MIGRATING")
    _service(svc, "node-1", forced_down=True)
    state["claim_result"] = "node-3"

    r = client.post("/servers/srv1/evacuate", json={"host": "node-3"})

    assert r.status_code == 202, r.text
    assert r.json()["from"] == "node-1" and r.json()["to"] == "node-3"
    (method, build), = state["casts"]
    assert method == "build_instance" and build["_target_status"] == "ACTIVE"
    assert _host(svc) == "node-3" and _status(svc) == "REBUILD"


def test_evacuate_refuses_a_migrating_server_whose_source_is_up(wired):
    svc, client, state, _ = wired
    _server(svc, "MIGRATING")

    r = client.post("/servers/srv1/evacuate", json={"host": "node-3"})

    assert r.status_code == 409 and "forced down" in r.json()["detail"]
    assert state["casts"] == [] and _status(svc) == "MIGRATING"


def test_cold_migrate_still_refuses_a_migrating_server(wired):
    svc, client, state, _ = wired
    _server(svc, "MIGRATING")

    r = client.post("/servers/srv1/migrate", json={"host": "node-3"})

    assert r.status_code == 409 and "MIGRATING" in r.json()["detail"]


# --- the finish hook settles host and status (I2) ------------------------------------------------------------------

def _finish(client, result, **extra):
    return client.post("/internal/servers/srv1/actions/finish",
                       json={"result": result, "action": "live_migrate", "message": None, **extra},
                       headers={"X-Internal-Token": "service-token"})


def _error_message(svc):
    with svc.SessionLocal() as db:
        return db.get(svc.Server, "srv1").error_message


def test_success_sets_the_target_host_and_active_when_the_conductor_update_was_lost(wired, internal):
    svc, client, state, _ = wired
    _server(svc, "MIGRATING", error_message="stale")

    r = _finish(client, "success", target_host="node-2")

    assert r.status_code == 200, r.text
    assert (_host(svc), _status(svc), _error_message(svc)) == ("node-2", "ACTIVE", None)
    assert state["restores"] == []


def test_success_without_a_target_host_leaves_the_record_alone(wired, internal):
    # an older worker sends no target_host: ACTIVE on the source host would let the target's reconciler kill the guest
    svc, client, _, _ = wired
    _server(svc, "MIGRATING")

    assert _finish(client, "success").status_code == 200

    assert (_host(svc), _status(svc)) == ("node-1", "MIGRATING")


def test_error_sets_active_on_the_source_and_restores_the_claim(wired, internal):
    svc, client, state, _ = wired
    _server(svc, "MIGRATING")

    assert _finish(client, "error", target_host="node-2").status_code == 200

    assert (_host(svc), _status(svc)) == ("node-1", "ACTIVE")
    assert state["restores"] == ["node-1"]


@pytest.mark.parametrize("status", ["ACTIVE", "DELETING", "DELETED", "SHUTOFF", "ERROR"])
@pytest.mark.parametrize("result", ["success", "error"])
def test_a_server_no_longer_migrating_is_not_changed(wired, internal, status, result):
    svc, client, _, _ = wired
    _server(svc, status)

    assert _finish(client, result, target_host="node-2").status_code == 200

    assert (_host(svc), _status(svc)) == ("node-1", status)


def test_other_actions_never_change_host_or_status(wired, internal):
    svc, client, _, _ = wired
    _server(svc, "MIGRATING")

    r = client.post("/internal/servers/srv1/actions/finish",
                    json={"result": "success", "action": "migrate", "target_host": "node-2"},
                    headers={"X-Internal-Token": "service-token"})

    assert r.status_code == 200
    assert (_host(svc), _status(svc)) == ("node-1", "MIGRATING")
