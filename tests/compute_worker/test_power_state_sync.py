
import pytest


def test_sync_power_states_corrects_missing_domain_to_shutoff(monkeypatch):
    """DB says ACTIVE, domain is gone from the hypervisor -> corrected to SHUTOFF."""
    fastapi = pytest.importorskip("fastapi")
    assert fastapi
    from services.compute_worker.app import main

    servers = [{"id": "srv-1", "name": "vmtest", "status": "ACTIVE",
                "libvirt_domain_name": "mini-os-vmtest-abc123"}]

    def fake_call(routing_key, method, args, timeout=None):
        assert routing_key == "conductor" and method == "get_host_servers"
        return {"servers": servers}

    updates = []
    monkeypatch.setattr(main._rpc, "call", fake_call)
    monkeypatch.setattr(main, "conductor_update_server",
                         lambda server_id, fields: updates.append((server_id, fields)))
    monkeypatch.setattr(main.libvirt_driver, "get_all_domain_states", lambda: {})  # domain missing entirely

    main.sync_power_states()

    assert updates == [("srv-1", {"status": "SHUTOFF"})]

def test_sync_power_states_corrects_shutoff_domain_to_shutoff(monkeypatch):
    """DB says ACTIVE, libvirt reports the domain SHUTOFF -> corrected to SHUTOFF."""
    fastapi = pytest.importorskip("fastapi")
    assert fastapi
    from services.compute_worker.app import main

    servers = [{"id": "srv-1", "name": "vmtest", "status": "ACTIVE",
                "libvirt_domain_name": "mini-os-vmtest-abc123"}]

    monkeypatch.setattr(main._rpc, "call", lambda *a, **kw: {"servers": servers})
    updates = []
    monkeypatch.setattr(main, "conductor_update_server",
                         lambda server_id, fields: updates.append((server_id, fields)))
    monkeypatch.setattr(main.libvirt_driver, "get_all_domain_states",
                         lambda: {"mini-os-vmtest-abc123": "SHUTOFF"})

    main.sync_power_states()

    assert updates == [("srv-1", {"status": "SHUTOFF"})]

def test_sync_power_states_ignores_transitional_status(monkeypatch):
    """A server mid-BUILD/DELETING/etc. is owned by an RPC handler — never touched."""
    fastapi = pytest.importorskip("fastapi")
    assert fastapi
    from services.compute_worker.app import main

    servers = [{"id": "srv-1", "name": "vmtest", "status": "BUILD",
                "libvirt_domain_name": None}]

    monkeypatch.setattr(main._rpc, "call", lambda *a, **kw: {"servers": servers})
    updates = []
    monkeypatch.setattr(main, "conductor_update_server",
                         lambda server_id, fields: updates.append((server_id, fields)))
    monkeypatch.setattr(main.libvirt_driver, "get_all_domain_states", lambda: {})

    main.sync_power_states()

    assert updates == []

def test_sync_power_states_no_op_when_status_already_matches(monkeypatch):
    """Domain RUNNING and DB already ACTIVE — nothing to correct."""
    fastapi = pytest.importorskip("fastapi")
    assert fastapi
    from services.compute_worker.app import main

    servers = [{"id": "srv-1", "name": "vmtest", "status": "ACTIVE",
                "libvirt_domain_name": "mini-os-vmtest-abc123"}]

    monkeypatch.setattr(main._rpc, "call", lambda *a, **kw: {"servers": servers})
    updates = []
    monkeypatch.setattr(main, "conductor_update_server",
                         lambda server_id, fields: updates.append((server_id, fields)))
    monkeypatch.setattr(main.libvirt_driver, "get_all_domain_states",
                         lambda: {"mini-os-vmtest-abc123": "RUNNING"})

    main.sync_power_states()

    assert updates == []

def test_sync_power_states_recovers_shutoff_to_active(monkeypatch):
    """DB says SHUTOFF but the domain is actually RUNNING again -> corrected to ACTIVE."""
    fastapi = pytest.importorskip("fastapi")
    assert fastapi
    from services.compute_worker.app import main

    servers = [{"id": "srv-1", "name": "vmtest", "status": "SHUTOFF",
                "libvirt_domain_name": "mini-os-vmtest-abc123"}]

    monkeypatch.setattr(main._rpc, "call", lambda *a, **kw: {"servers": servers})
    updates = []
    monkeypatch.setattr(main, "conductor_update_server",
                         lambda server_id, fields: updates.append((server_id, fields)))
    monkeypatch.setattr(main.libvirt_driver, "get_all_domain_states",
                         lambda: {"mini-os-vmtest-abc123": "RUNNING"})

    main.sync_power_states()

    assert updates == [("srv-1", {"status": "ACTIVE"})]
