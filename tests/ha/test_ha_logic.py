import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "services"))

from shared.ha_logic import SHARED_CEPH, is_stale, next_state, partition  # noqa: E402

NOW = datetime(2026, 10, 19, 12, 0, 0)


def test_stale_boundary():
    assert not is_stale(NOW - timedelta(seconds=60), NOW)
    assert is_stale(NOW - timedelta(seconds=61), NOW)


def test_partition_mixed():
    servers = [
        {"id": "ok", "host": "h1", "status": "ACTIVE"},
        {"id": "bad-vol", "host": "h1", "status": "ACTIVE"},
        {"id": "unknown-vol", "host": "h1", "status": "ACTIVE"},
        {"id": "unprotected", "host": "h1", "status": "ACTIVE"},
        {"id": "elsewhere", "host": "h2", "status": "ACTIVE"},
    ]
    protected = {"ok", "bad-vol", "unknown-vol", "elsewhere"}
    vols = {"ok": ["h3", "h2"], "bad-vol": ["h3", "h1"], "unknown-vol": [None], "unprotected": ["h3"], "elsewhere": ["h3"]}
    ids, skipped = partition(servers, protected, "h1", vols)
    assert ids == ["ok"]
    assert dict(skipped) == {"bad-vol": "volume on failed host", "unknown-vol": "volume host unknown"}


def test_partition_skips_a_server_that_is_live_migrating():
    # the guest may already run on the live-migration target; evacuating would start a second copy
    servers = [
        {"id": "moving", "host": "h1", "status": "MIGRATING"},
        {"id": "ok", "host": "h1", "status": "ACTIVE"},
    ]
    vols = {"moving": ["h3"], "ok": ["h3"]}
    ids, skipped = partition(servers, {"moving", "ok"}, "h1", vols)
    assert ids == ["ok"]
    assert dict(skipped) == {"moving": "live migration in progress; check the target before evacuating"}


def test_partition_a_ceph_volume_is_on_no_host_and_never_on_the_failed_one():
    servers = [{"id": n, "host": "h1", "status": "ACTIVE"} for n in ("ceph", "mixed", "mixed-bad", "unknown")]
    vols = {"ceph": [SHARED_CEPH], "mixed": [SHARED_CEPH, "h3"], "mixed-bad": [SHARED_CEPH, "h1"], "unknown": [SHARED_CEPH, None]}
    ids, skipped = partition(servers, set(vols), "h1", vols)
    assert ids == ["ceph", "mixed"]
    assert dict(skipped) == {"mixed-bad": "volume on failed host", "unknown": "volume host unknown"}


def test_partition_server_without_volumes_is_skipped():
    ids, skipped = partition([{"id": "a", "host": "h1", "status": "ACTIVE"}], {"a"}, "h1", {})
    assert ids == [] and dict(skipped) == {"a": "volume host unknown"}


def test_next_state_matrix():
    assert next_state("up", True, False, 0) == "suspect"
    assert next_state("up", False, False, 0) == "up"
    assert next_state("suspect", False, True, 0) == "up"
    assert next_state("suspect", True, False, 0) == "suspect"
    assert next_state("suspect", True, True, 0) == "recovering"
    assert next_state("suspect", True, True, 2) == "recovering"
    assert next_state("suspect", True, True, 3) == "suspect"
    assert next_state("fenced", False, True, 0) == "fenced"
    assert next_state("fenced", True, True, 0) == "fenced"
    assert next_state("recovering", True, True, 0) == "recovering"
