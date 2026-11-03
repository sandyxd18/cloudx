"""Shelving a server offloads it: the domain, its taps and the volume sessions go, the records stay. It used to call a
block-store route that does not exist (404, only a warning), so the VM was stopped and called offloaded while the
domain, the taps and the sessions were all still on the node."""
import pytest

pytest.importorskip("fastapi")

from services.compute_worker.app import main  # noqa: E402

SERVER = {"id": "srv-1", "libvirt_domain_name": "mini-os-vm-srv-1", "_root_volume_id": "vol-root",
          "_volume_ids": ["vol-root", "vol-data"], "_block_store_url": "http://bs.test:8004"}


@pytest.fixture
def node(monkeypatch):
    seen = {"deleted": [], "disconnected": [], "updates": [], "finished": [], "posts": []}
    monkeypatch.setattr(main.worker, "delete_server", lambda sid, args: seen["deleted"].append(sid))
    monkeypatch.setattr(main, "_probe_block_store_url", lambda url: url)
    monkeypatch.setattr(main, "_volume_host", lambda url, vid: f"stor-{vid}")
    monkeypatch.setattr(main, "_disconnect_volume", lambda vid, host: seen["disconnected"].append((vid, host)))
    monkeypatch.setattr(main, "_http_post", lambda url, body=None, **k: seen["posts"].append(url))
    monkeypatch.setattr(main, "conductor_update_server", lambda sid, fields: seen["updates"].append((sid, fields)))
    monkeypatch.setattr(main, "conductor_mark_error", lambda sid, msg: seen["updates"].append((sid, {"status": "ERROR", "msg": msg})))
    monkeypatch.setattr(main, "_notify_action_finish",
                        lambda sid, result="success", message=None, action=None: seen["finished"].append((sid, result, action)))
    return seen


def test_the_domain_is_removed_and_every_volume_session_is_dropped(node):
    main.handle_shelve(dict(SERVER))

    assert node["deleted"] == ["srv-1"]
    assert node["disconnected"] == [("vol-root", "stor-vol-root"), ("vol-data", "stor-vol-data")]


def test_the_volumes_stay_attached_to_the_server_record(node):
    main.handle_shelve(dict(SERVER))

    assert not any("detach" in url for url in node["posts"]), "unshelve finds the data volumes by their attachment"


def test_the_server_ends_up_offloaded_and_the_action_is_reported_as_shelve(node):
    main.handle_shelve(dict(SERVER))

    assert node["updates"] == [("srv-1", {"status": "SHELVED_OFFLOADED", "error_message": None})]
    assert node["finished"] == [("srv-1", "success", "shelve")]


def test_a_domain_that_cannot_be_removed_leaves_the_server_in_error_not_offloaded(node, monkeypatch):
    def boom(sid, args):
        raise RuntimeError("libvirt is down")

    monkeypatch.setattr(main.worker, "delete_server", boom)

    main.handle_shelve(dict(SERVER))

    assert node["updates"][0][1]["status"] == "ERROR" and "libvirt is down" in node["updates"][0][1]["msg"]
    assert node["disconnected"] == []
    assert node["finished"] == [("srv-1", "error", "shelve")]
