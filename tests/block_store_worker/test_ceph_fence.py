"""Cut a dead node's Ceph client off an image: blocklist every watcher of the image (the image has layering only, so
there is no exclusive lock whose owner could be named)."""
import json

import pytest

from services.block_store_worker.app import main as worker
from services.block_store_worker.app.drivers import ceph

ADDR1, ADDR2 = "172.20.0.178:0/3122052869", "172.20.0.178:0/99"


@pytest.fixture
def cluster(monkeypatch):
    monkeypatch.setattr(ceph, "CEPH_POOL", "volumes")
    state = {"watchers": [], "rbd": [], "ceph": [], "ceph_error": None}

    def rbd(*args, **kw):
        state["rbd"].append(list(args))
        return json.dumps({"watchers": [{"id": 1, "address": a, "client": 7, "cookie": 9} for a in state["watchers"]]})

    def cli(*args, **kw):
        state["ceph"].append(list(args))
        if state["ceph_error"]:
            raise RuntimeError(state["ceph_error"])
        return ""

    monkeypatch.setattr(ceph, "_rbd", rbd)
    monkeypatch.setattr(ceph, "_ceph", cli)
    return state


def test_every_watcher_of_the_image_is_blocklisted(cluster):
    cluster["watchers"] = [ADDR1, ADDR2]
    assert ceph.fence("vol-abc") == "ok"
    assert cluster["rbd"] == [["status", "volumes/vol-abc", "--format", "json"]]
    assert cluster["ceph"] == [["osd", "blocklist", "add", ADDR1], ["osd", "blocklist", "add", ADDR2]]


def test_an_image_nobody_has_open_has_nothing_to_blocklist(cluster):
    assert ceph.fence("vol-abc") == "no-watcher"
    assert cluster["ceph"] == []


def test_a_refused_blocklist_is_raised_so_the_fence_counts_as_failed(cluster):
    cluster["watchers"] = [ADDR1]
    cluster["ceph_error"] = "Error EACCES: access denied"
    with pytest.raises(RuntimeError, match="EACCES"):
        ceph.fence("vol-abc")


def test_the_ceph_cli_uses_the_same_cluster_settings_as_rbd(monkeypatch):
    seen = []
    monkeypatch.setattr(ceph, "output", lambda cmd, timeout=None: seen.append(cmd) or "")
    monkeypatch.setattr(ceph, "CEPH_CONF", "/etc/ceph/ceph.conf")
    monkeypatch.setattr(ceph, "CEPH_USER", "cloudx")
    monkeypatch.setattr(ceph, "CEPH_KEYRING", "/etc/ceph/k")
    ceph._ceph("osd", "blocklist", "add", ADDR1)
    assert seen == [["ceph", "--conf", "/etc/ceph/ceph.conf", "--id", "cloudx", "--keyring", "/etc/ceph/k",
                     "osd", "blocklist", "add", ADDR1]]


def test_the_worker_answers_fence_volume_with_the_result_for_the_volumes_image(monkeypatch):
    seen = []
    monkeypatch.setattr(worker.ceph, "fence", lambda image: seen.append(image) or "no-watcher")
    vid = "0123456789ab-cdef-0123456789abcdef"
    assert worker.HANDLERS["fence_volume"]({"volume_id": vid}) == {"result": "no-watcher"}
    assert seen == [worker._lv_name(vid)]
