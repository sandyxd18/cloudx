"""A server that is MIGRATING has its domain on two hosts for a while: the stale-domain reconciler must not destroy it,
and a worker restart mid-migration must not leave a server that is still running here MIGRATING forever."""
import pytest

pytest.importorskip("fastapi")
from services.compute_worker.app import main  # noqa: E402


class _Servers:
    status_code = 200

    def __init__(self, *servers):
        self._servers = list(servers)

    def json(self):
        return {"servers": self._servers}


@pytest.fixture
def env(monkeypatch):
    seen = {"deleted": [], "updates": [], "notified": [], "aborts": [], "jobs": {}, "abortable": True, "after": {}}
    monkeypatch.setattr(main, "COMPUTE_HOST", "node-a")
    monkeypatch.setenv("COMPUTE_API_URL", "http://compute.test:8003")
    monkeypatch.setattr(main.libvirt_driver, "iscsi_sessions", lambda n: [])
    monkeypatch.setattr(main.libvirt_driver, "interface_taps", lambda n: [])
    monkeypatch.setattr(main.libvirt_driver, "delete", lambda n: seen["deleted"].append(n))
    monkeypatch.setattr(main.network_manager, "delete_tap", lambda t: None)
    monkeypatch.setattr(main, "conductor_update_server", lambda sid, fields: seen["updates"].append((sid, fields)))
    monkeypatch.setattr(main, "_notify_action_finish", lambda *a, **k: seen["notified"].append(a + ((k,) if k else ())))
    monkeypatch.setattr(main, "RECOVER_ABORT_WAIT_SECONDS", 0.2)
    monkeypatch.setattr(main, "_RECOVER_POLL_SECONDS", 0.01)

    def abort_job(name):
        seen["aborts"].append(name)
        if seen["abortable"]:
            seen["jobs"][name] = False

    monkeypatch.setattr(main.libvirt_driver, "active_job", lambda name: seen["jobs"].get(name, False))
    monkeypatch.setattr(main.libvirt_driver, "abort_job", abort_job)
    monkeypatch.setattr(main.libvirt_driver, "domain_state", lambda name: seen["after"].get(name, "RUNNING"))

    def setup(servers, domains):
        monkeypatch.setattr(main.libvirt_driver, "get_all_domain_states", lambda: domains)
        monkeypatch.setattr(main.requests, "get", lambda *a, **k: _Servers(*servers))
    seen["setup"] = setup
    return seen


def _server(status, host, domain="vm-1", sid="srv-1"):
    return {"id": sid, "status": status, "host": host, "libvirt_domain_name": domain}


def test_reconcile_skips_a_domain_whose_server_is_migrating(env):
    env["setup"]([_server("MIGRATING", "node-b")], {"vm-1": "RUNNING"})

    main.reconcile_stale_domains()

    assert env["deleted"] == []


def test_reconcile_still_destroys_a_stale_active_domain(env):
    env["setup"]([_server("ACTIVE", "node-b")], {"vm-1": "RUNNING"})

    main.reconcile_stale_domains()

    assert env["deleted"] == ["vm-1"]


ABORTED = ("srv-1", "error", main.RECOVERED_MESSAGE, "live_migrate")


def test_recover_returns_a_running_migrating_server_to_active(env):
    env["setup"]([_server("MIGRATING", "node-a")], {"vm-1": "RUNNING"})

    main.recover_migrating_servers()

    assert env["updates"] == [("srv-1", {"status": "ACTIVE", "error_message": None})]
    # the error finish moves the placement claim back to this node (it went to the target when the migration began)
    assert env["notified"] == [ABORTED]
    assert env["aborts"] == []
    assert "restarted during the live migration" in main.RECOVERED_MESSAGE and "aborted" in main.RECOVERED_MESSAGE


def test_recover_leaves_a_server_whose_domain_is_not_here(env):
    env["setup"]([_server("MIGRATING", "node-a")], {})

    main.recover_migrating_servers()

    assert env["updates"] == []


def test_recover_ignores_servers_of_other_hosts_and_never_raises(env, monkeypatch):
    env["setup"]([_server("MIGRATING", "node-b"), _server("ACTIVE", "node-a", "vm-2", "srv-2")],
                 {"vm-1": "RUNNING", "vm-2": "RUNNING"})

    main.recover_migrating_servers()

    assert env["updates"] == []

    def boom(*a, **k):
        raise RuntimeError("api down")
    monkeypatch.setattr(main.requests, "get", boom)

    main.recover_migrating_servers()  # returns without an error

    assert env["updates"] == []


def test_recover_aborts_a_job_still_running_then_frees_the_server(env):
    env["setup"]([_server("MIGRATING", "node-a")], {"vm-1": "RUNNING"})
    env["jobs"]["vm-1"] = True

    main.recover_migrating_servers()

    assert env["aborts"] == ["vm-1"]
    assert env["updates"] == [("srv-1", {"status": "ACTIVE", "error_message": None})]
    assert env["notified"] == [ABORTED]


def test_recover_leaves_the_server_migrating_when_the_job_cannot_be_stopped(env, caplog):
    env["setup"]([_server("MIGRATING", "node-a")], {"vm-1": "RUNNING"})
    env["jobs"]["vm-1"] = True
    env["abortable"] = False

    with caplog.at_level("WARNING", logger="compute-worker"):
        main.recover_migrating_servers()

    assert env["aborts"] == ["vm-1"]
    assert env["updates"] == [] and env["notified"] == []
    assert "srv-1" in caplog.text and "MIGRATING" in caplog.text


def test_recover_leaves_a_domain_that_left_while_the_job_was_aborted(env):
    # the move completed as the abort arrived: the guest now runs on the target, so ACTIVE here would let the
    # target's reconciler destroy it
    env["setup"]([_server("MIGRATING", "node-a")], {"vm-1": "RUNNING"})
    env["jobs"]["vm-1"] = True
    env["after"]["vm-1"] = None

    main.recover_migrating_servers()

    assert env["updates"] == [] and env["notified"] == []


@pytest.mark.parametrize("state", ["PAUSED", "SHUTOFF", "SUSPENDED"])
def test_recover_leaves_a_server_whose_local_domain_is_not_running(env, state):
    env["setup"]([_server("MIGRATING", "node-a")], {"vm-1": state})

    main.recover_migrating_servers()

    assert env["updates"] == [] and env["notified"] == [] and env["aborts"] == []


def test_recover_does_nothing_when_the_api_answers_non_200(env, monkeypatch):
    class Down(_Servers):
        status_code = 503

    env["setup"]([], {"vm-1": "RUNNING"})
    monkeypatch.setattr(main.requests, "get", lambda *a, **k: Down(_server("MIGRATING", "node-a")))

    main.recover_migrating_servers()

    assert env["updates"] == [] and env["notified"] == []


def test_one_failing_server_does_not_stop_the_others(env, monkeypatch):
    env["setup"]([_server("MIGRATING", "node-a", "vm-1", "srv-1"), _server("MIGRATING", "node-a", "vm-2", "srv-2")],
                 {"vm-1": "RUNNING", "vm-2": "RUNNING"})

    def active_job(name):
        if name == "vm-1":
            raise RuntimeError("libvirt hiccup")
        return False

    monkeypatch.setattr(main.libvirt_driver, "active_job", active_job)

    main.recover_migrating_servers()

    assert env["updates"] == [("srv-2", {"status": "ACTIVE", "error_message": None})]
    assert [n[0] for n in env["notified"]] == ["srv-2"]


# --- follow-up A7: only a live migration is recovered here, and its own action row is named -------------------

def test_recover_leaves_a_cold_migrating_server_alone(env):
    # a cold migrate is MIGRATING with host = target and the build's domain running here; it has no open live_migrate
    server = {**_server("MIGRATING", "node-a"), "live_migrate_action_id": None}
    env["setup"]([server], {"vm-1": "RUNNING"})

    main.recover_migrating_servers()

    assert env["updates"] == [] and env["notified"] == [] and env["aborts"] == []


def test_recover_names_the_open_live_migrate_action(env):
    server = {**_server("MIGRATING", "node-a"), "live_migrate_action_id": "act-7"}
    env["setup"]([server], {"vm-1": "RUNNING"})

    main.recover_migrating_servers()

    assert env["updates"] == [("srv-1", {"status": "ACTIVE", "error_message": None})]
    assert env["notified"] == [ABORTED + ({"action_id": "act-7"},)]


def test_notify_action_finish_sends_the_action_id(monkeypatch):
    sent = []
    monkeypatch.setenv("COMPUTE_API_URL", "http://compute.test:8003")
    monkeypatch.setattr(main.requests, "post", lambda url, json=None, **k: sent.append((url, json)))

    main._notify_action_finish("srv-1", "error", "boom", "live_migrate", action_id="act-7")
    main._notify_action_finish("srv-1", "error", "boom", "live_migrate")

    assert sent[0][1] == {"result": "error", "message": "boom", "action": "live_migrate", "action_id": "act-7"}
    assert "action_id" not in sent[1][1]
