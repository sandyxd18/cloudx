import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "services"))

from shared.ha_logic import is_stale, next_state, partition  # noqa: E402

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
