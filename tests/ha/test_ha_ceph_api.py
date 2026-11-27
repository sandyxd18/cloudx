"""The ha-api client of the block-store: what it makes of a Ceph volume and of the answers of the fence call."""
import pytest

from services.ha.app import monitor
from shared.ha_logic import SHARED_CEPH


def volume(vid, sid="s1", host=None, backend="lvm"):
    return {"id": vid, "attached_to_server_id": sid, "host": host, "backend": backend}


@pytest.fixture
def api(monkeypatch):
    a = monitor.Api()
    a.answers = {}
    monkeypatch.setattr(a, "call", lambda svc, method, path, body=None: a.answers[(method, path)])
    return a


def test_a_ceph_volume_has_no_host_but_counts_as_shared_storage(api):
    api.answers[("GET", "/volumes")] = {"items": [
        volume("a", backend="ceph"), volume("b", host="n2"), volume("c", host=None), volume("d", sid="other", host="n9"),
        volume("e", backend="Ceph")]}
    assert api.server_volume_hosts("s1") == [SHARED_CEPH, "n2", None, SHARED_CEPH]  # an LVM volume with no host stays unknown


def test_fence_without_a_client_to_cut_off_is_reported_not_raised(api):
    api.answers[("POST", "/internal/fence")] = {"results": {"a": "ok", "b": "no-watcher"}}
    assert api.fence("", ["a", "b"]) == ["b"]


def test_fence_with_every_volume_cut_off_reports_nothing(api):
    api.answers[("POST", "/internal/fence")] = {"results": {"a": "ok"}}
    assert api.fence("iqn.x", ["a"]) == []


def test_a_fence_error_still_raises(api):
    api.answers[("POST", "/internal/fence")] = {"results": {"a": "ok", "b": "error: boom", "c": "no-watcher"}}
    with pytest.raises(monitor.Failure, match="b"):
        api.fence("iqn.x", ["a", "b", "c"])
