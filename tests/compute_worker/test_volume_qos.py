"""A volume type's `max_iops` and `max_throughput` (MB/s) limit a disk that is not an RBD image through libvirt's
`<iotune>`: the compute node throttles it in QEMU, whether the LV is local or reached over iSCSI. Ceph keeps its own
limit on the RBD image (block-store-worker sets it), so an RBD disk gets no `<iotune>`.
"""
import xml.etree.ElementTree as ET

import pytest

from services.compute_worker.app import libvirt_driver, volume_manager, worker
from services.compute_worker.app import main as worker_main


def iotune(xml):
    """{tag: int} of the first <iotune> of the XML, or None when it has none."""
    node = ET.fromstring(xml).find(".//iotune")
    return None if node is None else {child.tag: int(child.text) for child in node}


def test_a_data_lv_with_both_limits_gets_an_iotune():
    xml = libvirt_driver.disk_device_xml("/dev/vg/vol-1", "sdb", "raw", max_iops=500, max_throughput=20)
    assert iotune(xml) == {"total_bytes_sec": 20 * 1024 * 1024, "total_iops_sec": 500}


@pytest.mark.parametrize("limits, expected", [
    ({"max_iops": 300}, {"total_iops_sec": 300}),
    ({"max_throughput": 10}, {"total_bytes_sec": 10 * 1024 * 1024}),
])
def test_one_limit_alone_sets_only_its_own_element(limits, expected):
    assert iotune(libvirt_driver.disk_device_xml("/dev/vg/vol-1", "sdb", "raw", **limits)) == expected


@pytest.mark.parametrize("limits", [{}, {"max_iops": None, "max_throughput": None}, {"max_iops": 0, "max_throughput": 0}])
def test_no_limit_or_a_zero_limit_means_no_iotune(limits):
    assert iotune(libvirt_driver.disk_device_xml("/dev/vg/vol-1", "sdb", "raw", **limits)) is None


def test_the_iotune_stays_inside_the_disk_element():
    disk = ET.fromstring(libvirt_driver.disk_device_xml("/dev/vg/vol-1", "sdb", "raw", max_iops=100))
    assert disk.tag == "disk" and disk.find("iotune/total_iops_sec").text == "100"


def test_an_rbd_disk_gets_no_iotune_because_ceph_enforces_the_limit_on_the_image():
    xml = libvirt_driver.disk_device_xml("rbd:volumes/vol-1", "sdb", "raw", max_iops=500, max_throughput=20)
    assert iotune(xml) is None


def test_a_file_disk_is_limited_too():
    xml = libvirt_driver.disk_device_xml("/var/lib/cloudx/vol-1.qcow2", "sdb", "qcow2", max_iops=100)
    assert iotune(xml) == {"total_iops_sec": 100}


def test_the_root_disk_of_a_new_domain_is_limited():
    xml = libvirt_driver.domain_xml("vm", 512, 1, "/dev/vg/root", "/i/seed.iso", "tap0", "02:00:00:00:00:01",
                                    root_max_iops=800, root_max_throughput=40)
    assert iotune(xml) == {"total_bytes_sec": 40 * 1024 * 1024, "total_iops_sec": 800}
    root = ET.fromstring(xml).find(".//disk[@device='disk']")
    assert root.find("target").get("dev") == "sda" and root.find("iotune") is not None


def test_the_seed_cdrom_and_a_domain_without_limits_have_no_iotune():
    xml = libvirt_driver.domain_xml("vm", 512, 1, "/dev/vg/root", "/i/seed.iso", "tap0", "02:00:00:00:00:01")
    assert iotune(xml) is None


# ── hotplug attach ────────────────────────────────────────────────────────────

@pytest.fixture
def attached(monkeypatch):
    seen = []
    monkeypatch.setattr(volume_manager, "COMPUTE_HOST", "node-a")
    monkeypatch.setattr(volume_manager.libvirt_driver, "attach_disk", lambda *a, **k: seen.append((a, k)))
    monkeypatch.setattr(volume_manager, "connect", lambda volume, rpc: type("P", (), {"suffix": "", "exists": lambda self: True, "__str__": lambda self: "/dev/vg/vol-1"})())
    return seen


def payload(**volume):
    return {"libvirt_domain_name": "vm-1", "volume": {"volume_id": "vol-1", "path": "/dev/vg/vol-1", "host": "node-a", "target_dev": "sdb", **volume}}


def test_attach_passes_the_volumes_limits_to_libvirt(attached):
    volume_manager.attach_volume(payload(max_iops=500, max_throughput=20), rpc=None)
    (_, kwargs), = attached
    assert kwargs == {"max_iops": 500, "max_throughput": 20}


def test_attach_without_limits_calls_libvirt_as_before(attached):
    volume_manager.attach_volume(payload(), rpc=None)
    (args, kwargs), = attached
    assert kwargs == {} and len(args) == 4


# ── a new server ──────────────────────────────────────────────────────────────

def spawn_payload(**root_volume):
    return {
        "name": "vm1",
        "flavor_snapshot": {"vcpus": 1, "ram_mb": 512, "disk_gb": 1},
        "root_volume": {"path": "/dev/vg/root", **root_volume},
        "port": {"logical_port_name": "lp1", "mac_address": "02:00:00:00:00:01", "fixed_ip": "10.0.0.5"},
    }


@pytest.fixture
def spawn_env(tmp_path, monkeypatch):
    seed = tmp_path / "seed.iso"
    seed.write_bytes(b"")
    defined = []
    monkeypatch.setattr(worker, "ensure_host_ready", lambda: None)
    monkeypatch.setattr(worker, "INSTANCE_DIR", tmp_path / "instances")
    monkeypatch.setattr(worker.Path, "exists", lambda self: True)
    monkeypatch.setattr(worker, "expose_to_host_libvirt", lambda *a, **k: None)
    monkeypatch.setattr(worker.cloudinit, "create_config_drive", lambda *a, **k: seed)
    monkeypatch.setattr(worker.network_manager, "create_tap", lambda tap: None)
    monkeypatch.setattr(worker.network_manager, "bind_tap_to_ovs", lambda *a, **k: None)
    monkeypatch.setattr(worker.network_manager, "delete_tap", lambda tap: None)
    monkeypatch.setattr(worker.libvirt_driver, "define_and_start", defined.append)
    return defined


def test_spawn_limits_the_root_disk_with_the_root_volumes_limits(spawn_env):
    worker.spawn_server("srv-1", spawn_payload(max_iops=800, max_throughput=40))
    assert iotune(spawn_env[0]) == {"total_bytes_sec": 40 * 1024 * 1024, "total_iops_sec": 800}


def test_spawn_without_limits_defines_a_domain_without_iotune(spawn_env):
    worker.spawn_server("srv-1", spawn_payload())
    assert iotune(spawn_env[0]) is None


def test_the_build_handler_keeps_the_limits_of_the_polled_volume(monkeypatch):
    """compute-api may send a root_volume dict of its own; the path is taken from the polled record, and so must the limits be."""
    spawned = []
    ready = {"status": "available", "path": "/dev/vg/root", "host": None, "max_iops": 800, "max_throughput": 40}
    monkeypatch.setattr(worker_main, "endpoint", lambda *a, **k: "http://bs.test")
    monkeypatch.setattr(worker_main, "poll_root_volume", lambda *a: ready)
    monkeypatch.setattr(worker_main.volume_manager, "connect", lambda vol, rpc: "/dev/vg/root")
    monkeypatch.setattr(worker_main.worker, "spawn_server", lambda server_id, args: spawned.append(args["root_volume"]) or {})
    monkeypatch.setattr(worker_main, "conductor_update_server", lambda *a, **k: None)
    monkeypatch.setattr(worker_main, "_notify_action_finish", lambda *a, **k: None)
    monkeypatch.setattr(worker_main, "_probe_block_store_url", lambda url: url)
    monkeypatch.setattr(worker_main, "_http_post", lambda *a, **k: {})

    worker_main.handle_build_instance({"id": "srv-1", "_root_volume_id": "vol-1", "root_volume": {"id": "vol-1", "path": ""}})

    assert spawned[0]["max_iops"] == 800 and spawned[0]["max_throughput"] == 40


def test_a_data_volume_reattached_by_a_build_is_limited_too(monkeypatch):
    attached = []
    monkeypatch.setattr(worker_main, "endpoint", lambda *a, **k: "http://bs.test")
    monkeypatch.setattr(worker_main, "_probe_block_store_url", lambda url: url)
    monkeypatch.setattr(worker_main, "poll_root_volume", lambda *a: {"status": "available", "path": "/dev/vg/root", "host": None})
    monkeypatch.setattr(worker_main.volume_manager, "connect", lambda vol, rpc: "/dev/vg/data")
    monkeypatch.setattr(worker_main.worker, "spawn_server", lambda server_id, args: {"libvirt_domain_name": "vm-1"})
    monkeypatch.setattr(worker_main, "conductor_update_server", lambda *a, **k: None)
    monkeypatch.setattr(worker_main, "_notify_action_finish", lambda *a, **k: None)
    monkeypatch.setattr(worker_main, "_http_post", lambda *a, **k: {})
    monkeypatch.setattr(worker_main.libvirt_driver, "attach_disk", lambda *a, **k: attached.append((a, k)))

    class Reply:
        status_code = 200

        def json(self):
            return {"id": "vol-2", "path": "/dev/vg/data", "host": None, "target_device": "sdb", "max_iops": 100, "max_throughput": 5}

    monkeypatch.setattr(worker_main.requests, "get", lambda *a, **k: Reply())

    worker_main.handle_build_instance({"id": "srv-1", "_root_volume_id": "vol-1", "root_volume": {}, "_data_volumes": [{"id": "vol-2"}]})

    (_, kwargs), = attached
    assert kwargs["target_dev"] == "sdb" and kwargs["max_iops"] == 100 and kwargs["max_throughput"] == 5
