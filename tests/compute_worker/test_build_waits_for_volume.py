"""A build waits for its root volume without holding the node's queue: the RPC consumer runs one handler at a time,
so a handler that sleeps until the volume is ready stalls every other request for the node (delete, stop, console...)."""
import time

import pytest
from shared.rpc import host_queue

import services.compute_worker.app.main as worker_main
from services.compute_worker.app.config import COMPUTE_HOST


class StillPolling(BaseException):
    """Raised by the fake volume API when the handler asks again and again: it is waiting inside the handler."""


class Reply:
    def __init__(self, status_code, body):
        self.status_code, self._body = status_code, body

    def json(self):
        return self._body


@pytest.fixture
def build(monkeypatch):
    seen = {"cast": [], "errors": [], "spawned": [], "updates": [], "polls": 0}
    volume = {"status_code": 200, "body": {"status": "creating", "path": ""}}
    monkeypatch.setattr(worker_main, "endpoint", lambda *a, **k: "http://bs.test")
    monkeypatch.setattr(worker_main, "_probe_block_store_url", lambda url: url)

    def get(url, **kwargs):
        seen["polls"] += 1
        if seen["polls"] > 3:
            raise StillPolling()
        return Reply(volume["status_code"], volume["body"])

    monkeypatch.setattr(worker_main.requests, "get", get)
    monkeypatch.setattr(worker_main.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(worker_main._rpc, "cast", lambda queue, method, args: seen["cast"].append((queue, method, args)))
    monkeypatch.setattr(worker_main, "conductor_mark_error", lambda server_id, message: seen["errors"].append(message))
    monkeypatch.setattr(worker_main, "conductor_update_server", lambda server_id, fields: seen["updates"].append(fields))
    monkeypatch.setattr(worker_main, "_notify_action_finish", lambda *a, **k: None)
    monkeypatch.setattr(worker_main, "_disconnect_volume", lambda *a, **k: None)
    monkeypatch.setattr(worker_main, "_http_post", lambda *a, **k: {})
    monkeypatch.setattr(worker_main.volume_manager, "connect", lambda vol, rpc: "/dev/vol")
    monkeypatch.setattr(worker_main.worker, "spawn_server", lambda server_id, args: seen["spawned"].append(server_id) or {})

    def run(args=None, **volume_state):
        volume.update(volume_state)
        worker_main.handle_build_instance({"id": "srv-1", "_root_volume_id": "vol-1", "_block_store_url": "http://bs.test",
                                           "root_volume": {}, **(args or {})})
        return seen

    return run


def test_a_volume_still_being_created_sends_the_build_to_the_back_of_the_queue(build):
    seen = build()
    assert seen["spawned"] == [] and seen["errors"] == []
    (queue, method, args), = seen["cast"]
    assert (queue, method) == (host_queue("compute", COMPUTE_HOST), "build_instance")
    assert args["id"] == "srv-1" and args["_volume_wait_deadline"] > time.time()


def test_the_deadline_is_set_once_and_kept_on_every_round(build):
    deadline = time.time() + 120
    seen = build({"_volume_wait_deadline": deadline})
    assert seen["cast"][0][2]["_volume_wait_deadline"] == deadline


def test_a_volume_that_never_comes_ready_ends_in_error_after_the_deadline(build):
    seen = build({"_volume_wait_deadline": time.time() - 1})
    assert seen["cast"] == [] and seen["spawned"] == []
    assert len(seen["errors"]) == 1 and "Timed out waiting for volume vol-1" in seen["errors"][0]


def test_a_volume_that_is_ready_is_built_at_once(build):
    seen = build(body={"status": "available", "path": "/dev/vg/vol-1"})
    assert seen["spawned"] == ["srv-1"] and seen["cast"] == [] and seen["errors"] == []


def test_a_volume_in_error_fails_the_build(build):
    seen = build(body={"status": "error", "error_message": "no space"})
    assert seen["spawned"] == [] and seen["cast"] == []
    assert "entered error state: no space" in seen["errors"][0]


def test_a_volume_deleted_while_waiting_drops_the_build(build):
    # the server was deleted in between: its delete handler removed the root volume
    seen = build(status_code=404, body={})
    assert seen["spawned"] == [] and seen["cast"] == [] and seen["errors"] == []
