"""A volume whose create request was lost must not stay in 'creating' forever (found in the controller failure tests)."""
from datetime import datetime, timedelta, timezone

from services.block_store_worker.app import reconciler


class FakeRpc:
    def __init__(self, volumes):
        self.volumes, self.casts = list(volumes), []

    def call(self, routing_key, method, args, timeout=None):
        return {"volumes": self.volumes} if method == "list_all_volumes" else {"snapshots": []}

    def cast(self, routing_key, method, args):
        self.casts.append((method, args))


def creating(vol_id, age_seconds, backend="ceph", created_at=None):
    made = datetime.utcnow() - timedelta(seconds=age_seconds)
    return {"id": vol_id, "name": vol_id, "status": "creating", "backend": backend, "host": None, "path": "",
            "created_at": made if created_at is None else created_at}


def flagged(rpc):
    return {args["volume_id"]: args for method, args in rpc.casts if method == "mark_volume_error"}


def test_a_creating_volume_without_backend_object_is_marked_error_once_it_is_too_old(monkeypatch):
    monkeypatch.setattr(reconciler, "CREATING_TIMEOUT", 3600)
    monkeypatch.setattr(reconciler.ceph, "exists", lambda name: False)
    rpc = FakeRpc([creating("stuck", 3601)])

    reconciler.reconcile_volumes(rpc)

    assert list(flagged(rpc)) == ["stuck"]
    message = flagged(rpc)["stuck"]["error_message"]
    assert "3600" in message and "create it again" in message


def test_a_young_creating_volume_is_left_to_its_handler(monkeypatch):
    monkeypatch.setattr(reconciler, "CREATING_TIMEOUT", 3600)
    monkeypatch.setattr(reconciler.ceph, "exists", lambda name: False)
    rpc = FakeRpc([creating("busy", 120)])

    reconciler.reconcile_volumes(rpc)

    assert rpc.casts == []


def test_an_old_creating_volume_whose_backend_object_exists_is_not_touched(monkeypatch):
    # the handler may still be converting an image into it: the object exists from the start
    monkeypatch.setattr(reconciler, "CREATING_TIMEOUT", 3600)
    monkeypatch.setattr(reconciler.ceph, "exists", lambda name: True)
    rpc = FakeRpc([creating("converting", 7200)])

    reconciler.reconcile_volumes(rpc)

    assert rpc.casts == []


def test_an_unreachable_backend_never_marks_a_creating_volume(monkeypatch):
    monkeypatch.setattr(reconciler, "CREATING_TIMEOUT", 3600)

    def broken(name):
        raise RuntimeError("rbd timed out")

    monkeypatch.setattr(reconciler.ceph, "exists", broken)
    rpc = FakeRpc([creating("unknown", 7200)])

    reconciler.reconcile_volumes(rpc)

    assert rpc.casts == []


def test_created_at_formats_the_rpc_layer_may_deliver(monkeypatch):
    # json.dumps(default=str) turns a datetime into 'YYYY-MM-DD HH:MM:SS[.ffffff]'; an ISO string and an aware time are fine too
    monkeypatch.setattr(reconciler, "CREATING_TIMEOUT", 3600)
    monkeypatch.setattr(reconciler.ceph, "exists", lambda name: False)
    old = datetime.utcnow() - timedelta(hours=2)
    rpc = FakeRpc([
        creating("as-str", 0, created_at=str(old)),
        creating("as-iso", 0, created_at=old.isoformat()),
        creating("as-aware", 0, created_at=datetime.now(timezone.utc) - timedelta(hours=2)),
    ])

    reconciler.reconcile_volumes(rpc)

    assert set(flagged(rpc)) == {"as-str", "as-iso", "as-aware"}


def test_a_creating_volume_with_no_or_unreadable_created_at_is_never_judged(monkeypatch):
    monkeypatch.setattr(reconciler, "CREATING_TIMEOUT", 3600)
    monkeypatch.setattr(reconciler.ceph, "exists", lambda name: False)
    missing = creating("no-date", 0)
    del missing["created_at"]
    rpc = FakeRpc([missing, creating("garbled", 0, created_at="not a date"), creating("empty", 0, created_at=None)])

    reconciler.reconcile_volumes(rpc)

    assert rpc.casts == []


def test_a_timeout_of_zero_switches_the_check_off(monkeypatch):
    monkeypatch.setattr(reconciler, "CREATING_TIMEOUT", 0)
    monkeypatch.setattr(reconciler.ceph, "exists", lambda name: False)
    rpc = FakeRpc([creating("ancient", 10 ** 7)])

    reconciler.reconcile_volumes(rpc)

    assert rpc.casts == []


def test_another_nodes_lvm_volume_is_still_not_ours_to_judge(monkeypatch):
    monkeypatch.setattr(reconciler, "CREATING_TIMEOUT", 3600)
    monkeypatch.setattr(reconciler, "BLOCK_STORE_HOST", "node-a")
    monkeypatch.setattr(reconciler.lvm, "exists", lambda name: False)
    theirs = {**creating("theirs", 7200, backend="lvm"), "host": "node-b"}
    rpc = FakeRpc([theirs])

    reconciler.reconcile_volumes(rpc)

    assert rpc.casts == []
