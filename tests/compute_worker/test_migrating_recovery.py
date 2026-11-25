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
    seen = {"deleted": [], "updates": []}
    monkeypatch.setattr(main, "COMPUTE_HOST", "node-a")
    monkeypatch.setenv("COMPUTE_API_URL", "http://compute.test:8003")
    monkeypatch.setattr(main.libvirt_driver, "iscsi_sessions", lambda n: [])
    monkeypatch.setattr(main.libvirt_driver, "interface_taps", lambda n: [])
    monkeypatch.setattr(main.libvirt_driver, "delete", lambda n: seen["deleted"].append(n))
    monkeypatch.setattr(main.network_manager, "delete_tap", lambda t: None)
    monkeypatch.setattr(main, "conductor_update_server", lambda sid, fields: seen["updates"].append((sid, fields)))

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


def test_recover_returns_a_running_migrating_server_to_active(env):
    env["setup"]([_server("MIGRATING", "node-a")], {"vm-1": "RUNNING"})

    main.recover_migrating_servers()

    assert env["updates"] == [("srv-1", {"status": "ACTIVE", "error_message": None})]


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
