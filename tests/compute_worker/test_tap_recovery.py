"""A host reboot removes the kernel tap devices of the VMs on it, but the libvirt domains (managed='no' targets) and the
OVS ports survive. Starting such a domain fails with "target managed='no' but specified dev doesn't exist" unless the
tap is created again first, without touching the OVS port that carries the OVN iface-id."""
from types import SimpleNamespace

import pytest

DOMAIN_XML = """
<domain type="kvm">
  <name>mini-os-vm-12345678</name>
  <devices>
    <interface type="ethernet">
      <target dev="tap-mini-aaaaaa" managed="no"/>
    </interface>
    <interface type="ethernet">
      <target dev="tap-mini-bbbbbb" managed="no"/>
    </interface>
    <interface type="bridge">
      <target dev="vnet0"/>
    </interface>
  </devices>
</domain>
"""


def _fake_run(commands, missing_links=(), fail_on=None):
    """Records every command; `ip link show dev X` fails until X is added, and `fail_on` makes one command fail."""
    missing = set(missing_links)

    def run(cmd, check=True, **kwargs):
        commands.append(list(cmd))
        if cmd[:3] == ["ip", "link", "show"] and cmd[-1] in missing:
            return SimpleNamespace(returncode=1, stdout="", stderr="does not exist")
        if fail_on is not None and cmd[:len(fail_on)] == fail_on:
            return SimpleNamespace(returncode=1, stdout="", stderr="Operation not permitted")
        if cmd[:3] == ["ip", "tuntap", "add"]:
            missing.discard(cmd[4])
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    return run


def test_ensure_tap_creates_a_missing_tap_and_leaves_the_ovs_port_alone(monkeypatch):
    from services.compute_worker.app import network_manager

    commands = []
    monkeypatch.setattr(network_manager, "run", _fake_run(commands, missing_links={"tap-mini-aaaaaa"}))

    network_manager.ensure_tap("tap-mini-aaaaaa")

    assert ["ip", "tuntap", "add", "dev", "tap-mini-aaaaaa", "mode", "tap"] in commands
    assert ["ip", "link", "set", "tap-mini-aaaaaa", "up"] in commands
    assert not [c for c in commands if c[0] == "ovs-vsctl"], "del-port would drop the iface-id the OVN port is bound to"


def test_ensure_tap_does_nothing_when_the_tap_exists(monkeypatch):
    from services.compute_worker.app import network_manager

    commands = []
    monkeypatch.setattr(network_manager, "run", _fake_run(commands))

    network_manager.ensure_tap("tap-mini-aaaaaa")

    assert commands == [["ip", "link", "show", "dev", "tap-mini-aaaaaa"]]


def test_ensure_tap_names_the_tap_when_it_cannot_be_created(monkeypatch):
    from services.compute_worker.app import network_manager

    commands = []
    monkeypatch.setattr(
        network_manager, "run",
        _fake_run(commands, missing_links={"tap-mini-aaaaaa"}, fail_on=["ip", "tuntap", "add"]),
    )

    with pytest.raises(RuntimeError, match="tap-mini-aaaaaa"):
        network_manager.ensure_tap("tap-mini-aaaaaa")


class _FakeDomain:
    def __init__(self, events, active=False, xml=DOMAIN_XML):
        self._events, self._active, self._xml = events, active, xml

    def isActive(self):
        return self._active

    def XMLDesc(self, flags=0):
        return self._xml

    def create(self):
        self._events.append("create")


class _FakeConn:
    def close(self):
        return None


def _patch_domain(monkeypatch, domain_obj, events):
    from services.compute_worker.app import libvirt_driver, network_manager

    monkeypatch.setattr(libvirt_driver, "lookup", lambda name: (_FakeConn(), domain_obj))
    monkeypatch.setattr(network_manager, "ensure_tap", lambda name: events.append(f"tap:{name}"))
    return libvirt_driver


def test_start_recreates_every_unmanaged_tap_before_the_domain_is_created(monkeypatch):
    events = []
    driver = _patch_domain(monkeypatch, _FakeDomain(events), events)

    driver.start("mini-os-vm-12345678")

    assert events == ["tap:tap-mini-aaaaaa", "tap:tap-mini-bbbbbb", "create"]


def test_start_leaves_a_running_domain_alone(monkeypatch):
    events = []
    driver = _patch_domain(monkeypatch, _FakeDomain(events, active=True), events)

    driver.start("mini-os-vm-12345678")

    assert events == []


def test_resume_from_disk_recreates_the_taps_before_restoring(monkeypatch):
    events = []
    driver = _patch_domain(monkeypatch, _FakeDomain(events), events)

    driver.resume_from_disk("mini-os-vm-12345678")

    assert events == ["tap:tap-mini-aaaaaa", "tap:tap-mini-bbbbbb", "create"]
