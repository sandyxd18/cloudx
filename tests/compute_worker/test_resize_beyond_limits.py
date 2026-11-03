"""A domain is defined with <vcpu> and <memory> equal to its flavor, and libvirt refuses to hot-plug past those maximums
("requested vcpus is greater than max allowable vcpus"). Growing a server to a bigger flavor therefore ended in ERROR.
Inside the maximums the change stays live; beyond them the persistent definition gets the new limits and the domain is
restarted, which is what a resize to a larger flavor has to do anyway."""
import xml.etree.ElementTree as ET

import pytest

pytest.importorskip("fastapi")

from services.compute_worker.app import libvirt_driver  # noqa: E402


def _xml(vcpus=1, memory_kib=524288, current_vcpus=None):
    current = f" current='{current_vcpus}'" if current_vcpus else ""
    return f"""
<domain type="kvm">
  <name>mini-os-vm-12345678</name>
  <memory unit='KiB'>{memory_kib}</memory>
  <currentMemory unit='KiB'>{memory_kib}</currentMemory>
  <vcpu placement='static'{current}>{vcpus}</vcpu>
  <devices>
    <interface type="ethernet"><target dev="tap-mini-aaaaaa" managed="no"/></interface>
  </devices>
</domain>
"""


class FakeDomain:
    def __init__(self, events, xml, active=True):
        self.events, self.xml, self._active = events, xml, active
        self.max_vcpus = int(ET.fromstring(xml).findtext("vcpu"))
        self.max_kib = int(ET.fromstring(xml).findtext("memory"))

    def isActive(self):
        return self._active

    def XMLDesc(self, flags=0):
        return self.xml

    def setVcpusFlags(self, count, flags):
        if count > self.max_vcpus:
            raise RuntimeError(f"invalid argument: requested vcpus is greater than max allowable vcpus for the domain: {count} > {self.max_vcpus}")
        self.events.append(("vcpus", count, flags))

    def setMemoryFlags(self, kib, flags):
        if kib > self.max_kib:
            raise RuntimeError("invalid argument: cannot set memory higher than max memory")
        self.events.append(("memory", kib, flags))

    def shutdown(self):
        self._active = False
        self.events.append("shutdown")

    def destroy(self):
        self._active = False
        self.events.append("destroy")

    def hasManagedSaveImage(self, flags=0):
        return False

    def create(self):
        self._active = True
        self.events.append("create")


class FakeConn:
    def __init__(self, domain, events):
        self._domain, self._events = domain, events

    def lookupByName(self, name):
        return self._domain

    def defineXML(self, xml):
        self._events.append(("define", xml))
        self._domain.xml = xml
        return self._domain

    def close(self):
        return None


@pytest.fixture
def host(monkeypatch):
    events = []

    def use(domain):
        monkeypatch.setattr(libvirt_driver, "connect", lambda: FakeConn(domain, events))

    monkeypatch.setattr(libvirt_driver.network_manager, "ensure_tap", lambda tap: events.append(f"tap:{tap}"))
    monkeypatch.setattr("time.sleep", lambda s: None)
    return events, use


def _defined(events):
    return [e[1] for e in events if isinstance(e, tuple) and e[0] == "define"]


def test_a_resize_inside_the_limits_stays_live(host):
    events, use = host
    use(FakeDomain(events, _xml(vcpus=4, memory_kib=2097152)))

    libvirt_driver.resize("mini-os-vm-12345678", 2, 1024)

    assert [e[:2] for e in events if isinstance(e, tuple)] == [("vcpus", 2), ("memory", 1024 * 1024)]
    assert "destroy" not in events and "shutdown" not in events and not _defined(events)


def test_more_vcpus_than_the_domain_was_defined_with_redefines_it_with_the_new_maximum(host):
    events, use = host
    use(FakeDomain(events, _xml(vcpus=1)))

    libvirt_driver.resize("mini-os-vm-12345678", 2, None)

    root = ET.fromstring(_defined(events)[0])
    assert root.findtext("vcpu") == "2"
    assert root.find("vcpu").get("current") in (None, "2")


def test_more_memory_than_the_domain_was_defined_with_raises_both_memory_elements(host):
    events, use = host
    use(FakeDomain(events, _xml(memory_kib=524288)))

    libvirt_driver.resize("mini-os-vm-12345678", None, 1024)

    root = ET.fromstring(_defined(events)[0])
    assert (root.findtext("memory"), root.find("memory").get("unit")) == ("1024", "MiB")
    assert (root.findtext("currentMemory"), root.find("currentMemory").get("unit")) == ("1024", "MiB")


def test_a_running_domain_is_shut_down_and_started_from_the_new_definition(host):
    events, use = host
    use(FakeDomain(events, _xml(vcpus=1), active=True))

    libvirt_driver.resize("mini-os-vm-12345678", 2, 1024)

    assert "destroy" not in events, "a guest that answers the ACPI shutdown is not killed"
    kinds = [e if isinstance(e, str) else e[0] for e in events]
    assert kinds.index("shutdown") < kinds.index("define") < kinds.index("create")
    assert "tap:tap-mini-aaaaaa" in events


def test_a_guest_that_ignores_the_shutdown_is_destroyed_after_the_wait(host, monkeypatch):
    events, use = host

    class Stubborn(FakeDomain):
        def shutdown(self):
            self.events.append("shutdown")

    use(Stubborn(events, _xml(vcpus=1), active=True))
    monkeypatch.setattr(libvirt_driver, "RESIZE_SHUTDOWN_WAIT", 0, raising=False)

    libvirt_driver.resize("mini-os-vm-12345678", 2, None)

    kinds = [e if isinstance(e, str) else e[0] for e in events]
    assert kinds.index("shutdown") < kinds.index("destroy") < kinds.index("define") < kinds.index("create")


def test_a_stopped_domain_is_redefined_and_left_stopped(host):
    events, use = host
    use(FakeDomain(events, _xml(vcpus=1), active=False))

    libvirt_driver.resize("mini-os-vm-12345678", 2, None)

    assert _defined(events)
    assert "create" not in events and "shutdown" not in events and "destroy" not in events


def test_a_shrink_of_one_value_and_a_growth_of_the_other_redefines_with_both(host):
    events, use = host
    use(FakeDomain(events, _xml(vcpus=2, memory_kib=2097152)))

    libvirt_driver.resize("mini-os-vm-12345678", 4, 512)

    root = ET.fromstring(_defined(events)[0])
    assert root.findtext("vcpu") == "4" and root.findtext("memory") == "512"
