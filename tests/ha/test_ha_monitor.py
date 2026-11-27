import importlib
from datetime import datetime, timedelta

import pytest
from shared.ha_logic import SHARED_CEPH

T0 = datetime(2026, 10, 19, 12, 0, 0)


class Fake:
    def __init__(self):
        self.calls = []
        self.srv = [{"id": "ok", "host": "h1", "status": "ACTIVE"}]
        self.vh = {"ok": ["h2"]}
        self.fence_fails = False
        self.unfenced = []  # volumes on which fence found no Ceph client
        self.status = "ACTIVE"

    def servers(self):
        return self.srv

    def server_volume_hosts(self, sid):
        return self.vh.get(sid, [])

    def server_volume_ids(self, sid):
        return [f"vol-{sid}"]

    def force_down(self, host):
        self.calls.append(("force_down", host))

    def fence(self, iqn, ids):
        self.calls.append(("fence", iqn, tuple(ids)))
        if self.fence_fails:
            raise RuntimeError("fence boom")
        return self.unfenced

    def evacuate(self, sid):
        self.calls.append(("evacuate", sid))

    def server_status(self, sid):
        return self.status


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'ha.sqlite'}")
    import services.ha.app.main as m
    m = importlib.reload(m)
    mon = importlib.reload(importlib.import_module("services.ha.app.monitor"))
    m.monitor = mon
    mon.VERIFY, mon.CONFIRM, mon.WAIT = 0, 30, 0
    m.Base.metadata.create_all(bind=m.engine)
    db = m.SessionLocal()
    db.add(m.Host(host="h1", initiator_iqn="iqn.h1", last_seen=T0 - timedelta(seconds=120), state="up", fails=0))
    db.add(m.Protected(server_id="ok", project_id="p1"))
    db.commit()
    api = Fake()
    return m, mon, db, api


def state(m, db):
    db.expire_all()
    return db.get(m.Host, "h1").state


def kinds(api):
    return [c[0] for c in api.calls]


def test_blip_no_action(env):
    m, mon, db, api = env
    mon.cycle(db, api, T0)
    assert state(m, db) == "suspect"
    db.get(m.Host, "h1").last_seen = T0 + timedelta(seconds=5)  # heartbeat is back
    db.commit()
    mon.cycle(db, api, T0 + timedelta(seconds=40))
    assert state(m, db) == "up" and api.calls == []


def test_no_confirm_before_window(env):
    m, mon, db, api = env
    mon.cycle(db, api, T0)
    mon.cycle(db, api, T0 + timedelta(seconds=10))
    assert api.calls == [] and state(m, db) == "suspect"


def test_dead_host_no_protected_only_force_down(env):
    m, mon, db, api = env
    db.query(m.Protected).delete()
    db.commit()
    mon.cycle(db, api, T0)
    mon.cycle(db, api, T0 + timedelta(seconds=31))
    assert api.calls == [("force_down", "h1")] and state(m, db) == "fenced"


def test_mixed_partition_evacuates_safe_only(env):
    m, mon, db, api = env
    api.srv += [{"id": "bad", "host": "h1", "status": "ACTIVE"}]
    api.vh["bad"] = ["h1"]
    db.add(m.Protected(server_id="bad", project_id="p1"))
    db.commit()
    mon.cycle(db, api, T0)
    mon.cycle(db, api, T0 + timedelta(seconds=31))
    assert ("evacuate", "ok") in api.calls and ("evacuate", "bad") not in api.calls
    rows = {r.server_id: r for r in db.query(m.Recovery)}
    assert rows["ok"].status == "done" and rows["bad"].status == "skipped" and "failed host" in rows["bad"].message
    assert state(m, db) == "fenced"


def test_a_live_migrating_server_is_never_evacuated_by_ha(env):
    m, mon, db, api = env
    api.srv += [{"id": "moving", "host": "h1", "status": "MIGRATING"}]
    api.vh["moving"] = ["h2"]
    db.add(m.Protected(server_id="moving", project_id="p1"))
    db.commit()
    mon.cycle(db, api, T0)
    mon.cycle(db, api, T0 + timedelta(seconds=31))
    assert ("evacuate", "moving") not in api.calls and ("evacuate", "ok") in api.calls
    # its volumes are not fenced either: the target of the migration may be using them
    assert all("vol-moving" not in c[2] for c in api.calls if c[0] == "fence")
    rows = {r.server_id: r for r in db.query(m.Recovery)}
    assert rows["moving"].status == "skipped" and "live migration" in rows["moving"].message
    assert state(m, db) == "fenced"


def test_force_down_before_fence_before_evacuate(env):
    m, mon, db, api = env
    mon.cycle(db, api, T0)
    mon.cycle(db, api, T0 + timedelta(seconds=31))
    assert kinds(api) == ["force_down", "fence", "evacuate"]
    assert api.calls[1] == ("fence", "iqn.h1", ("vol-ok",))


def test_fence_failure_no_evacuate_and_stops_after_3(env):
    m, mon, db, api = env
    api.fence_fails = True
    mon.cycle(db, api, T0)
    for i in range(1, 7):
        mon.cycle(db, api, T0 + timedelta(seconds=30 + i))
    assert "evacuate" not in kinds(api)
    assert kinds(api).count("fence") == 3
    assert state(m, db) == "suspect" and db.get(m.Host, "h1").fails == 3


def test_no_iqn_skipped(env):
    m, mon, db, api = env
    db.get(m.Host, "h1").initiator_iqn = None
    db.commit()
    mon.cycle(db, api, T0)
    mon.cycle(db, api, T0 + timedelta(seconds=31))
    assert "evacuate" not in kinds(api) and "fence" not in kinds(api)
    assert db.query(m.Recovery).one().status == "skipped"
    assert state(m, db) == "fenced"


def test_evacuate_not_active_marks_failed(env):
    m, mon, db, api = env
    api.status = "ERROR"
    mon.cycle(db, api, T0)
    mon.cycle(db, api, T0 + timedelta(seconds=31))
    assert db.query(m.Recovery).one().status == "failed"


def test_fenced_host_ignored_after_heartbeat_returns(env):
    m, mon, db, api = env
    mon.cycle(db, api, T0)
    mon.cycle(db, api, T0 + timedelta(seconds=31))
    n = len(api.calls)
    db.get(m.Host, "h1").last_seen = T0 + timedelta(seconds=60)
    db.commit()
    mon.cycle(db, api, T0 + timedelta(seconds=70))
    assert state(m, db) == "fenced" and len(api.calls) == n


def test_recovery_notes_missing_watchdog(env):
    m, mon, db, api = env
    h = db.get(m.Host, "h1")
    mon.recover(db, api, h)
    r = db.scalars(m.select(m.Recovery).where(m.Recovery.server_id == "ok")).one()
    assert r.status == "done" and "no watchdog seen" in r.message


def test_a_server_on_ceph_only_is_fenced_and_evacuated_without_an_iqn(env):
    m, mon, db, api = env
    api.vh["ok"] = [SHARED_CEPH]
    db.get(m.Host, "h1").initiator_iqn = None  # a Ceph-only node never reports one
    db.commit()
    mon.cycle(db, api, T0)
    mon.cycle(db, api, T0 + timedelta(seconds=31))
    assert kinds(api) == ["force_down", "fence", "evacuate"]
    assert api.calls[1] == ("fence", "", ("vol-ok",))
    assert db.query(m.Recovery).one().status == "done"


def test_a_server_with_lvm_and_ceph_volumes_still_needs_the_iqn(env):
    m, mon, db, api = env
    api.vh["ok"] = [SHARED_CEPH, "h2"]
    db.get(m.Host, "h1").initiator_iqn = None
    db.commit()
    mon.cycle(db, api, T0)
    mon.cycle(db, api, T0 + timedelta(seconds=31))
    assert "fence" not in kinds(api) and "evacuate" not in kinds(api)
    r = db.query(m.Recovery).one()
    assert r.status == "skipped" and "initiator iqn" in r.message


def test_only_the_servers_that_need_an_iqn_are_skipped_without_one(env):
    m, mon, db, api = env
    api.srv.append({"id": "ceph", "host": "h1", "status": "ACTIVE"})
    api.vh["ceph"] = [SHARED_CEPH]
    db.add(m.Protected(server_id="ceph", project_id="p1"))
    db.get(m.Host, "h1").initiator_iqn = None
    db.commit()
    mon.cycle(db, api, T0)
    mon.cycle(db, api, T0 + timedelta(seconds=31))
    rows = {r.server_id: r for r in db.query(m.Recovery)}
    assert rows["ok"].status == "skipped" and rows["ceph"].status == "done"
    assert [c for c in api.calls if c[0] == "fence"] == [("fence", "", ("vol-ceph",))]


def test_a_ceph_volume_with_no_client_to_cut_off_is_noted_and_the_server_still_moves(env):
    m, mon, db, api = env
    api.vh["ok"] = [SHARED_CEPH]
    api.unfenced = ["vol-ok"]
    mon.recover(db, api, db.get(m.Host, "h1"))
    r = db.scalars(m.select(m.Recovery).where(m.Recovery.server_id == "ok")).one()
    assert r.status == "done" and "no Ceph client" in r.message and "vol-ok" in r.message
    assert "evacuate" in kinds(api)


def test_a_stopped_server_is_done_when_it_is_shutoff_again_on_the_new_node(env):
    # evacuate rebuilds a SHUTOFF server as SHUTOFF (compute keeps the status it had): waiting for ACTIVE would mark a
    # successful recovery `failed` after VERIFY seconds
    m, mon, db, api = env
    api.srv[0]["status"] = "SHUTOFF"
    api.status = "SHUTOFF"
    mon.recover(db, api, db.get(m.Host, "h1"))
    r = db.scalars(m.select(m.Recovery).where(m.Recovery.server_id == "ok")).one()
    assert r.status == "done"


def test_a_stopped_server_that_ends_in_error_is_failed_with_the_status_it_should_have_reached(env):
    m, mon, db, api = env
    api.srv[0]["status"] = "SHUTOFF"
    api.status = "ERROR"
    mon.recover(db, api, db.get(m.Host, "h1"))
    r = db.scalars(m.select(m.Recovery).where(m.Recovery.server_id == "ok")).one()
    assert r.status == "failed" and "SHUTOFF" in r.message


def test_a_running_server_that_comes_back_stopped_is_still_failed(env):
    m, mon, db, api = env
    api.status = "SHUTOFF"  # the server was ACTIVE: ending SHUTOFF is not what was asked
    mon.recover(db, api, db.get(m.Host, "h1"))
    r = db.scalars(m.select(m.Recovery).where(m.Recovery.server_id == "ok")).one()
    assert r.status == "failed" and "ACTIVE" in r.message


def test_an_errored_server_is_expected_to_come_back_active(env):
    m, mon, db, api = env
    api.srv[0]["status"] = "ERROR"
    api.status = "ACTIVE"
    mon.recover(db, api, db.get(m.Host, "h1"))
    r = db.scalars(m.select(m.Recovery).where(m.Recovery.server_id == "ok")).one()
    assert r.status == "done"
