"""Follow-ups to live migration in compute-api: the cast names the action row it opened (`_action_id`), and a finish
that names an older row than the newest open live_migrate one only closes that row: it neither settles the server
nor moves the claim (a late "error" of migration #1 must not flip migration #2 to ACTIVE). The row is recorded inside
the guarded region, and /internal/servers tells a worker which servers have an open live_migrate action."""
from datetime import datetime, timedelta

import pytest

from tests.compute.test_catalog_failures import _status, api  # noqa: F401 (fixture)
from tests.compute.test_live_migrate import _host, _server, internal, wired  # noqa: F401 (fixtures)

TOKEN = {"X-Internal-Token": "service-token"}


def _row(svc, row_id, minutes_ago, finished=False):
    start = datetime.utcnow() - timedelta(minutes=minutes_ago)
    with svc.SessionLocal() as db:
        db.add(svc.ServerAction(id=row_id, server_id="srv1", action="live_migrate", start_time=start,
                                finish_time=start if finished else None, result="success" if finished else None))
        db.commit()


def _rows(svc):
    with svc.SessionLocal() as db:
        return {r.id: (r.result, r.finish_time is not None)
                for r in db.query(svc.ServerAction).filter_by(server_id="srv1")}


def _finish(client, result, **extra):
    return client.post("/internal/servers/srv1/actions/finish",
                       json={"result": result, "action": "live_migrate", "message": "m", **extra}, headers=TOKEN)


def test_the_cast_carries_the_id_of_the_action_row(wired):  # noqa: F811
    svc, client, state, _ = wired
    _server(svc)

    assert client.post("/servers/srv1/live-migrate", json={"host": "node-2"}).status_code == 202

    (_method, payload), = state["casts"]
    (action,) = client.get("/servers/srv1/os-instance-actions").json()["instanceActions"]
    assert payload["_action_id"] == action["request_id"]


@pytest.mark.parametrize("result", ["error", "success"])
def test_a_late_finish_of_an_older_migration_only_closes_its_own_row(wired, internal, result):  # noqa: F811
    svc, client, state, _ = wired
    _server(svc, "MIGRATING")           # migration #2 is running
    _row(svc, "act-1", minutes_ago=5)   # migration #1: the worker set ACTIVE, then its notify came late
    _row(svc, "act-2", minutes_ago=1)

    assert _finish(client, result, action_id="act-1", target_host="node-3").status_code == 200

    assert _rows(svc) == {"act-1": (result, True), "act-2": (None, False)}
    assert (_host(svc), _status(svc)) == ("node-1", "MIGRATING")
    assert state["restores"] == []


def test_a_finish_naming_the_newest_row_settles_as_before(wired, internal):  # noqa: F811
    svc, client, state, _ = wired
    _server(svc, "MIGRATING")
    _row(svc, "act-1", minutes_ago=5, finished=True)
    _row(svc, "act-2", minutes_ago=1)

    assert _finish(client, "error", action_id="act-2").status_code == 200

    assert _rows(svc)["act-2"] == ("error", True)
    assert _status(svc) == "ACTIVE"
    assert state["restores"] == ["node-1"]


def test_a_finish_naming_a_row_that_is_already_closed_changes_nothing(wired, internal):  # noqa: F811
    svc, client, state, _ = wired
    _server(svc, "MIGRATING")
    _row(svc, "act-1", minutes_ago=5, finished=True)

    assert _finish(client, "error", action_id="act-1").status_code == 200

    assert _rows(svc) == {"act-1": ("success", True)}
    assert _status(svc) == "MIGRATING" and state["restores"] == []


def test_a_finish_without_an_action_id_keeps_the_old_behaviour(wired, internal):  # noqa: F811
    svc, client, state, _ = wired
    _server(svc, "MIGRATING")
    _row(svc, "act-1", minutes_ago=5)
    _row(svc, "act-2", minutes_ago=1)

    assert _finish(client, "error").status_code == 200

    assert _rows(svc) == {"act-1": (None, False), "act-2": ("error", True)}   # the newest open row, as before
    assert _status(svc) == "ACTIVE" and state["restores"] == ["node-1"]


def test_a_failure_to_record_the_action_puts_the_status_and_the_claim_back(wired, monkeypatch):  # noqa: F811
    svc, client, state, ops = wired
    _server(svc)

    def broken(*a, **k):
        raise RuntimeError("database is gone")

    monkeypatch.setattr(ops, "record_action", broken)

    r = client.post("/servers/srv1/live-migrate", json={"host": "node-2"})

    assert r.status_code >= 500
    assert state["casts"] == []
    assert (_status(svc), _host(svc)) == ("ACTIVE", "node-1")
    assert state["restores"] == ["node-1"]


def test_internal_servers_names_the_open_live_migrate_action(wired, internal):  # noqa: F811
    svc, client, _, _ = wired
    _server(svc, "MIGRATING")
    _row(svc, "act-old", minutes_ago=5, finished=True)
    _row(svc, "act-1", minutes_ago=3)
    _row(svc, "act-2", minutes_ago=1)
    _server(svc, "MIGRATING", id="srv2", name="vm2", libvirt_domain_name="cloudx-srv2")  # a cold migrate

    servers = {s["id"]: s for s in client.get("/internal/servers", headers=TOKEN).json()["servers"]}

    assert servers["srv1"]["live_migrate_action_id"] == "act-2"
    assert servers["srv2"]["live_migrate_action_id"] is None
