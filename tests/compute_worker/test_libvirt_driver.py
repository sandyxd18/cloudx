
import pytest


def test_libvirt_domain_xml_generation():
    from services.compute_worker.app.libvirt_driver import domain_xml

    xml = domain_xml(
        name="mini-os-vm1-12345678",
        memory_mb=512,
        vcpus=1,
        disk_path="/instances/1/disk.qcow2",
        seed_iso="/instances/1/seed.iso",
        tap_name="tap-mini-123456",
        mac_address="02:00:00:00:00:01",
    )
    assert "<name>mini-os-vm1-12345678</name>" in xml
    assert 'type="kvm"' in xml
    assert 'type="scsi" model="virtio-scsi"' in xml
    assert 'file="/instances/1/disk.qcow2"' in xml
    assert 'dev="sda" bus="scsi"' in xml
    assert 'type="drive" controller="0" bus="0" target="0" unit="0"' in xml
    assert 'dev="tap-mini-123456" managed="no"' in xml
    assert 'address="02:00:00:00:00:01"' in xml

def test_volume_disk_xml_uses_scsi_hotplug_bus():
    from services.compute_worker.app.libvirt_driver import disk_device_xml
    from services.compute_worker.app.volume_manager import _target_dev

    assert _target_dev("volume-id").startswith("sd")
    xml = disk_device_xml("/volumes/vol1.qcow2", "sdb", "qcow2")
    assert 'target dev="sdb" bus="scsi"' in xml
    assert 'type="drive" controller="0" bus="0" target="0" unit="1"' in xml

def test_libvirt_get_all_domain_states_maps_states(monkeypatch):
    """get_all_domain_states() reduces libvirt's state ints to RUNNING/PAUSED/SHUTOFF."""
    pytest.importorskip("fastapi")
    from services.compute_worker.app import libvirt_driver

    class FakeDomain:
        def __init__(self, name, state_id):
            self._name = name
            self._state_id = state_id

        def name(self):
            return self._name

        def state(self):
            return (self._state_id, 0)

    class FakeConn:
        def listAllDomains(self):
            return [FakeDomain("vm-running", 1), FakeDomain("vm-paused", 3),
                    FakeDomain("vm-shutoff", 5), FakeDomain("vm-crashed", 6)]

        def close(self):
            return None

    monkeypatch.setattr(libvirt_driver, "libvirt", object())  # bypass "not installed" check
    monkeypatch.setattr(libvirt_driver, "connect", lambda: FakeConn())

    states = libvirt_driver.get_all_domain_states()

    assert states == {
        "vm-running": "RUNNING",
        "vm-paused": "PAUSED",
        "vm-shutoff": "SHUTOFF",
        "vm-crashed": "SHUTOFF",
    }

def test_libvirt_get_domain_raw_counters_reads_all_stat_sources(monkeypatch):
    """get_domain_raw_counters() combines info()/memoryStats()/interfaceStats()/blockStats()."""
    pytest.importorskip("fastapi")
    from services.compute_worker.app import libvirt_driver

    class FakeDomain:
        def isActive(self):
            return True

        def info(self):
            # (state, maxMem_kb, memory_kb, nrVirtCpu, cpuTime_ns)
            return (1, 1048576, 524288, 2, 4_000_000_000)

        def memoryStats(self):
            return {"available": 1048576, "unused": 524288}

        def interfaceStats(self, tap_name):
            assert tap_name == "tap-mini-abc123"
            return (1000, 10, 0, 0, 2000, 20, 0, 0)

        def blockStats(self, target_dev):
            assert target_dev == "sda"
            return (100, 4096000, 50, 2048000, 0)

    class FakeConn:
        def close(self):
            return None

    monkeypatch.setattr(libvirt_driver, "libvirt", object())
    monkeypatch.setattr(libvirt_driver, "lookup", lambda name: (FakeConn(), FakeDomain()))

    raw = libvirt_driver.get_domain_raw_counters("mini-os-vm1-abc123", "tap-mini-abc123")

    assert raw["cpu_time_ns"] == 4_000_000_000
    assert raw["nr_vcpus"] == 2
    assert raw["mem_total_kb"] == 1048576
    assert raw["mem_used_kb"] == 524288  # available - unused
    assert raw["net_rx_bytes"] == 1000 and raw["net_tx_bytes"] == 2000
    assert raw["disk_read_bytes"] == 4096000 and raw["disk_write_bytes"] == 2048000
    assert raw["disk_read_req"] == 100 and raw["disk_write_req"] == 50
    assert "timestamp" in raw

def test_libvirt_get_domain_raw_counters_returns_none_when_inactive(monkeypatch):
    """A defined-but-not-running domain has no live stats to read."""
    pytest.importorskip("fastapi")
    from services.compute_worker.app import libvirt_driver

    class FakeDomain:
        def isActive(self):
            return False

    class FakeConn:
        def close(self):
            return None

    monkeypatch.setattr(libvirt_driver, "libvirt", object())
    monkeypatch.setattr(libvirt_driver, "lookup", lambda name: (FakeConn(), FakeDomain()))

    assert libvirt_driver.get_domain_raw_counters("mini-os-vm1-abc123", "tap-mini-abc123") is None
