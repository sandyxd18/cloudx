"""rebuild, rescue and unrescue define the domain again. It must have one NIC per port of the server, and the config drive
must describe each of them, as when the server was built; the primary NIC alone left a server on two networks with one."""
import pytest

pytest.importorskip("fastapi")

from services.compute_worker.app import libvirt_driver, network_manager, worker  # noqa: E402

SERVER_ID = "d8456cae-b963-4252-8438-0962fe51312a"
PRIMARY = {"id": "a1b2c3d4-primary", "logical_port_name": "lp-main", "mac_address": "02:00:00:00:00:01", "fixed_ip": "10.10.0.5"}
EXTRA = {"id": "e5f6a7b8-extra", "logical_port_name": "lp-extra", "mac_address": "02:00:00:00:00:02", "fixed_ip": "10.20.0.5"}
EXTRA_TAP = network_manager.port_tap_name(SERVER_ID, EXTRA["id"])


@pytest.fixture
def host(tmp_path, monkeypatch):
    seen = {"drive": [], "rebuild": []}
    seed = tmp_path / "seed.iso"
    seed.write_bytes(b"")

    def drive(*args, **kwargs):
        seen["drive"].append({"args": args, "kwargs": kwargs})
        return seed

    def rebuild(*args, **kwargs):
        seen["rebuild"].append({"args": args, "kwargs": kwargs})

    monkeypatch.setattr(worker.cloudinit, "create_config_drive", drive)
    monkeypatch.setattr(worker.libvirt_driver, "rebuild", rebuild)
    monkeypatch.setattr(worker, "expose_to_host_libvirt", lambda *paths: None)
    from services.compute_worker.app import image_manager
    monkeypatch.setattr(image_manager, "download_and_prepare_disk", lambda image, path: None)
    return seen, tmp_path


def _payload(tmp_path, ports):
    return {
        "id": SERVER_ID, "name": "vm1", "libvirt_domain_name": "mini-os-vm1", "instance_dir": str(tmp_path),
        "tap_name": network_manager.tap_name(SERVER_ID), "mac_address": PRIMARY["mac_address"],
        "flavor_snapshot": {"ram_mb": 512, "vcpus": 1}, "image": {"id": "img2"}, "rescue_image_id": "img2",
        "disk_path": str(tmp_path / "root.qcow2"), "port": PRIMARY, "ports": ports,
    }


ACTIONS = [worker.rebuild_server, worker.rescue_server, worker.unrescue_server]


@pytest.mark.parametrize("action", ACTIONS, ids=lambda f: f.__name__)
def test_the_domain_gets_a_nic_for_every_extra_port(host, action):
    seen, tmp_path = host

    action(SERVER_ID, _payload(tmp_path, [PRIMARY, EXTRA]))

    (call,) = seen["rebuild"]
    assert call["kwargs"]["extra_interfaces"] == [(EXTRA_TAP, EXTRA["mac_address"])]


@pytest.mark.parametrize("action", ACTIONS, ids=lambda f: f.__name__)
def test_the_config_drive_describes_the_primary_port_and_every_extra_one(host, action):
    seen, tmp_path = host

    action(SERVER_ID, _payload(tmp_path, [PRIMARY, EXTRA]))

    (call,) = seen["drive"]
    assert PRIMARY in call["args"] or call["kwargs"].get("port") == PRIMARY
    assert call["kwargs"]["extra_ports"] == [EXTRA]


@pytest.mark.parametrize("action", ACTIONS, ids=lambda f: f.__name__)
def test_a_server_on_one_network_gets_no_extra_nic(host, action):
    seen, tmp_path = host

    action(SERVER_ID, _payload(tmp_path, [PRIMARY]))

    assert not seen["rebuild"][0]["kwargs"].get("extra_interfaces")
    assert not seen["drive"][0]["kwargs"].get("extra_ports")


class _Domain:
    def __init__(self, events, xml=""):
        self.events, self._xml = events, xml

    def isActive(self):
        return True

    def destroy(self):
        self.events.append("destroy")

    def undefineFlags(self, flags):
        self.events.append("undefine")

    def XMLDesc(self, flags=0):
        return self._xml

    def create(self):
        self.events.append("create")


class _Conn:
    def __init__(self, events):
        self.events, self.defined = events, []

    def lookupByName(self, name):
        return _Domain(self.events)

    def defineXML(self, xml):
        self.events.append("define")
        self.defined.append(xml)
        return _Domain(self.events, xml)

    def close(self):
        return None


def test_the_driver_defines_every_nic_and_makes_sure_each_tap_exists_before_it_starts(monkeypatch):
    events = []
    conn = _Conn(events)
    monkeypatch.setattr(libvirt_driver, "connect", lambda: conn)
    monkeypatch.setattr(libvirt_driver.network_manager, "ensure_tap", lambda tap: events.append(f"tap:{tap}"))

    libvirt_driver.rebuild(
        "mini-os-vm1", "/instances/root.qcow2", "/instances/seed.iso", "tap-mini-aaaaaa", "02:00:00:00:00:01", 512, 1,
        extra_interfaces=[("tap-mini-bbbbbb", "02:00:00:00:00:02")],
    )

    xml, = conn.defined
    assert 'dev="tap-mini-aaaaaa" managed="no"' in xml and 'dev="tap-mini-bbbbbb" managed="no"' in xml
    assert 'address="02:00:00:00:00:02"' in xml
    assert {"tap:tap-mini-aaaaaa", "tap:tap-mini-bbbbbb"} <= set(events)
    assert max(events.index("tap:tap-mini-aaaaaa"), events.index("tap:tap-mini-bbbbbb")) < events.index("create")


def test_the_driver_without_extra_interfaces_defines_the_primary_nic_only(monkeypatch):
    events = []
    conn = _Conn(events)
    monkeypatch.setattr(libvirt_driver, "connect", lambda: conn)
    monkeypatch.setattr(libvirt_driver.network_manager, "ensure_tap", lambda tap: None)

    libvirt_driver.rebuild("mini-os-vm1", "/instances/root.qcow2", "/instances/seed.iso", "tap-mini-aaaaaa", "02:00:00:00:00:01", 512, 1)

    xml, = conn.defined
    assert xml.count("<interface ") == 1
