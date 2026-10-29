"""A VM suspended to disk is shut off for libvirt. The power-state sync used to rewrite SUSPENDED to SHUTOFF within one
interval (30 s), so the status did not last and nothing could ask for "unsuspend"."""
import pytest

pytest.importorskip("fastapi")

from services.compute_worker.app import libvirt_driver  # noqa: E402
from services.compute_worker.app import main  # noqa: E402

DOM = "mini-os-vm-abc123"


class _Domain:
    def __init__(self, name, state_id, saved=False, save_error=None):
        self._name, self._state, self._saved, self._error = name, state_id, saved, save_error

    def name(self):
        return self._name

    def state(self):
        return self._state, 0

    def hasManagedSaveImage(self, flags):
        if self._error:
            raise self._error
        return self._saved


class _Conn:
    def __init__(self, domains):
        self._domains = domains

    def listAllDomains(self):
        return self._domains

    def close(self):
        pass


def _states(monkeypatch, *domains):
    monkeypatch.setattr(libvirt_driver, "libvirt", object())
    monkeypatch.setattr(libvirt_driver, "connect", lambda: _Conn(list(domains)))
    return libvirt_driver.get_all_domain_states()


def test_a_shut_off_domain_with_a_managed_save_is_suspended(monkeypatch):
    assert _states(
        monkeypatch,
        _Domain("run", 1), _Domain("paused", 3), _Domain("off", 5), _Domain("saved", 5, saved=True),
    ) == {"run": "RUNNING", "paused": "PAUSED", "off": "SHUTOFF", "saved": "SUSPENDED"}


def test_a_domain_whose_save_cannot_be_read_stays_shut_off(monkeypatch):
    assert _states(monkeypatch, _Domain("d", 5, save_error=RuntimeError("no"))) == {"d": "SHUTOFF"}


def _sync(monkeypatch, db_status, libvirt_state, auto_recovery=True, fenced=False):
    servers = [{"id": "srv-1", "name": "vm", "status": db_status, "libvirt_domain_name": DOM}]
    updates, started = [], []
    monkeypatch.setattr(main._rpc, "call", lambda *a, **kw: {"servers": servers})
    monkeypatch.setattr(main, "reconcile_stale_domains", lambda: None)
    monkeypatch.setattr(main, "conductor_update_server", lambda sid, fields: updates.append((sid, fields)))
    monkeypatch.setattr(main.libvirt_driver, "get_all_domain_states", lambda: {DOM: libvirt_state})
    monkeypatch.setattr(main.libvirt_driver, "start", lambda name: started.append(name))
    monkeypatch.setattr(main, "AUTO_RECOVERY", auto_recovery)

    class Flag:
        def exists(self):
            return fenced

    monkeypatch.setattr(main, "FENCED_FLAG", Flag())
    main.sync_power_states()
    return updates, started


def test_a_suspended_server_stays_suspended(monkeypatch):
    assert _sync(monkeypatch, "SUSPENDED", "SUSPENDED") == ([], [])


def test_a_suspended_server_whose_save_is_gone_is_shut_off(monkeypatch):
    # e.g. `virsh managedsave-remove`: nothing left to restore
    assert _sync(monkeypatch, "SUSPENDED", "SHUTOFF") == ([("srv-1", {"status": "SHUTOFF"})], [])


def test_active_in_the_db_but_suspended_on_the_host_is_restored(monkeypatch):
    # `resume` on a suspended server, or a host that managed-saved its guests on shutdown
    assert _sync(monkeypatch, "ACTIVE", "SUSPENDED") == ([], [DOM])


def test_without_auto_recovery_the_db_follows_the_host(monkeypatch):
    assert _sync(monkeypatch, "ACTIVE", "SUSPENDED", auto_recovery=False) == ([("srv-1", {"status": "SUSPENDED"})], [])


def test_a_fenced_node_restores_nothing(monkeypatch):
    assert _sync(monkeypatch, "ACTIVE", "SUSPENDED", fenced=True) == ([], [])
