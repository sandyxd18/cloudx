
import pytest


def test_tap_name_generation():
    from services.compute_worker.app.network_manager import tap_name

    generated = tap_name("12345678-aaaa-bbbb-cccc-000000000000")
    assert generated == "tap-mini-123456"
    assert len(generated) <= 15

def test_create_tap_reports_failed_tuntap(monkeypatch):
    from services.compute_worker.app import network_manager

    class FakeProc:
        def __init__(self, returncode=0, stderr=""):
            self.returncode = returncode
            self.stderr = stderr
            self.stdout = ""

    commands = []

    def fake_run(cmd, check=True):
        commands.append(cmd)
        if cmd[:4] == ["ip", "tuntap", "add", "dev"]:
            return FakeProc(1, "open: No such file or directory")
        return FakeProc()

    monkeypatch.setattr(network_manager, "run", fake_run)
    with pytest.raises(RuntimeError) as exc_info:
        network_manager.create_tap("tap-mini-test")
    assert "failed to create tap interface tap-mini-test" in str(exc_info.value)
    assert "/dev/net/tun" in str(exc_info.value)
    assert ["ovs-vsctl", "--if-exists", "del-port", "tap-mini-test"] in commands

def test_create_tap_verifies_visibility(monkeypatch):
    from services.compute_worker.app import network_manager

    class FakeProc:
        def __init__(self, returncode=0, stderr=""):
            self.returncode = returncode
            self.stderr = stderr
            self.stdout = ""

    def fake_run(cmd, check=True):
        if cmd[:4] == ["ip", "link", "show", "dev"]:
            return FakeProc(1, 'Device "tap-mini-test" does not exist.')
        return FakeProc()

    monkeypatch.setattr(network_manager, "run", fake_run)
    with pytest.raises(RuntimeError) as exc_info:
        network_manager.create_tap("tap-mini-test")
    assert "host network namespace" in str(exc_info.value)
