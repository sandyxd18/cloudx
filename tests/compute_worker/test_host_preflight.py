
import pytest


def test_compute_worker_host_ready_requires_ovs_bridge(monkeypatch):
    from services.compute_worker.app import worker

    monkeypatch.setattr(worker.Path, "exists", lambda self: True)
    monkeypatch.setattr(worker, "tool_available", lambda name: True)
    monkeypatch.setattr(worker, "succeeds", lambda cmd, **kwargs: False)  # br-exists says: no such bridge

    with pytest.raises(RuntimeError) as exc_info:
        worker.ensure_host_ready()
    assert "OVS bridge" in str(exc_info.value)
    assert "--create-br-int" in str(exc_info.value)

def test_compute_worker_health_preflight_mock(monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    assert fastapi
    from services.compute_worker.app import main

    class FakeConn:
        def close(self):
            return None

    monkeypatch.setattr(main.libvirt_driver, "connect", lambda: FakeConn())
    monkeypatch.setattr(main, "tool_available", lambda name: True)

    monkeypatch.setattr(main, "succeeds", lambda cmd, **kwargs: True)
    health = main.handle_health({})
    assert health["libvirt_connected"] is True
    assert health["ovs_available"] is True
    assert health["br_int_exists"] is True
    assert "tun_available" in health
