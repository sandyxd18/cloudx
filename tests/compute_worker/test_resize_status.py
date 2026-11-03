"""A resize that worked leaves the server in VERIFY_RESIZE for the user to confirm or revert; the revert, which is a
resize back, ends in ACTIVE. A failed one is ERROR as before."""
import pytest

pytest.importorskip("fastapi")

from services.compute_worker.app import main  # noqa: E402


@pytest.fixture
def conductor(monkeypatch):
    seen = {"updates": [], "errors": [], "finished": []}
    monkeypatch.setattr(main.worker, "resize_server", lambda args: seen.setdefault("resized", args))
    monkeypatch.setattr(main, "conductor_update_server", lambda sid, f: seen["updates"].append((sid, f["status"])))
    monkeypatch.setattr(main, "conductor_mark_error", lambda sid, msg: seen["errors"].append((sid, msg)))
    monkeypatch.setattr(main, "_notify_action_finish", lambda sid, result="success", message=None, action=None: seen["finished"].append((sid, result)))
    return seen


def test_a_finished_resize_waits_for_the_user(conductor):
    main.handle_resize({"id": "s1", "new_vcpus": 2, "new_ram_mb": 1024})

    assert conductor["updates"] == [("s1", "VERIFY_RESIZE")]
    assert conductor["finished"] == [("s1", "success")]


def test_a_finished_revert_is_active(conductor):
    main.handle_resize({"id": "s1", "new_vcpus": 1, "new_ram_mb": 512, "_revert": True})

    assert conductor["updates"] == [("s1", "ACTIVE")]


def test_a_failed_resize_is_an_error_and_keeps_the_snapshot_for_a_revert(conductor, monkeypatch):
    def boom(args):
        raise RuntimeError("libvirt is down")

    monkeypatch.setattr(main.worker, "resize_server", boom)

    main.handle_resize({"id": "s1", "new_vcpus": 2, "new_ram_mb": 1024})

    assert conductor["updates"] == [] and conductor["errors"][0][0] == "s1"
    assert conductor["finished"] == [("s1", "error")]


def test_a_revert_of_a_stopped_server_ends_stopped(conductor):
    main.handle_resize({"id": "s1", "new_vcpus": 1, "new_ram_mb": 512, "_revert": True, "_target_status": "SHUTOFF"})

    assert conductor["updates"] == [("s1", "SHUTOFF")]
