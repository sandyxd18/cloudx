from pathlib import Path

import pytest

from services.compute_worker.app import libvirt_driver, volume_manager, worker

CEPHADM_MON_HOST = (
    "[v2:172.18.0.2:3300/0,v1:172.18.0.2:6789/0] "
    "[v2:172.18.0.3:3300/0,v1:172.18.0.3:6789/0] "
    "[v2:172.18.0.4:3300/0,v1:172.18.0.4:6789/0]"
)
SECRET_UUID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def ceph_conf(tmp_path, monkeypatch):
    def write(mon_host):
        conf = tmp_path / "ceph.conf"
        conf.write_text(f"[global]\n\tfsid = x\n\tmon_host = {mon_host}\n")
        monkeypatch.setenv("CEPH_CONF", str(conf))
    write(CEPHADM_MON_HOST)
    return write


@pytest.fixture
def cephx(monkeypatch):
    monkeypatch.setenv("CEPH_SECRET_UUID", SECRET_UUID)
    monkeypatch.setenv("CEPH_USER", "cloudx")


def test_cephadm_mon_host_gives_one_v1_endpoint_per_monitor(ceph_conf):
    pool, image, hosts = libvirt_driver._rbd_parts("rbd:volumes/vol-1")
    assert (pool, image) == ("volumes", "vol-1")
    assert hosts == [("172.18.0.2", "6789"), ("172.18.0.3", "6789"), ("172.18.0.4", "6789")]


@pytest.mark.parametrize("raw, expected", [
    ("172.18.0.2,172.18.0.3", [("172.18.0.2", "6789"), ("172.18.0.3", "6789")]),
    ("172.18.0.2:6790", [("172.18.0.2", "6790")]),
    ("[v2:10.0.0.1:3300/0]", [("10.0.0.1", "3300")]),
    ("mon-a.lab", [("mon-a.lab", "6789")]),
])
def test_other_mon_host_spellings(ceph_conf, raw, expected):
    ceph_conf(raw)
    assert libvirt_driver._rbd_parts("rbd:volumes/v")[2] == expected


def test_snapshot_suffix_is_not_part_of_the_image_name(ceph_conf):
    assert libvirt_driver._rbd_parts("rbd:volumes/vol-1@snap")[:2] == ("volumes", "vol-1")


def test_root_disk_xml_lists_every_monitor_and_authenticates_with_the_libvirt_secret(ceph_conf, cephx):
    xml = libvirt_driver.domain_xml("vm", 512, 1, "rbd:volumes/vol-1", "/i/seed.iso", "tap0", "02:00:00:00:00:01")
    assert 'protocol="rbd" name="volumes/vol-1"' in xml
    for ip in ("172.18.0.2", "172.18.0.3", "172.18.0.4"):
        assert f'<host name="{ip}" port="6789" />' in xml
    assert f'<auth username="cloudx"><secret type="ceph" uuid="{SECRET_UUID}" /></auth>' in xml
    assert xml.index("<auth") < xml.index("<target dev=\"sda\"")


def test_data_disk_xml_has_the_same_monitors_and_auth(ceph_conf, cephx):
    xml = libvirt_driver.disk_device_xml("rbd:volumes/vol-2", "sdb", "raw")
    assert xml.count("<host ") == 3
    assert f'uuid="{SECRET_UUID}"' in xml
    assert 'target dev="sdb" bus="scsi"' in xml


def test_no_auth_element_without_a_secret(ceph_conf, monkeypatch):
    monkeypatch.delenv("CEPH_SECRET_UUID", raising=False)
    assert "<auth" not in libvirt_driver.disk_device_xml("rbd:volumes/vol-2", "sdb", "raw")


def spawn_payload(path):
    return {
        "name": "vm1",
        "flavor_snapshot": {"vcpus": 1, "ram_mb": 512, "disk_gb": 1},
        "root_volume": {"path": path},
        "port": {"logical_port_name": "lp1", "mac_address": "02:00:00:00:00:01", "fixed_ip": "10.0.0.5"},
    }


@pytest.fixture
def spawn_env(tmp_path, monkeypatch):
    seed = tmp_path / "seed.iso"
    seed.write_bytes(b"")
    defined = []
    monkeypatch.setattr(worker, "ensure_host_ready", lambda: None)
    monkeypatch.setattr(worker, "INSTANCE_DIR", tmp_path / "instances")
    monkeypatch.setattr(worker.cloudinit, "create_config_drive", lambda *a, **k: seed)
    monkeypatch.setattr(worker.network_manager, "create_tap", lambda tap: None)
    monkeypatch.setattr(worker.network_manager, "bind_tap_to_ovs", lambda *a, **k: None)
    monkeypatch.setattr(worker.network_manager, "delete_tap", lambda tap: None)
    monkeypatch.setattr(worker.libvirt_driver, "define_and_start", defined.append)
    return defined


def test_a_vm_can_boot_from_an_rbd_root_volume(spawn_env, ceph_conf, cephx):
    result = worker.spawn_server("srv-1", spawn_payload("rbd:volumes/vol-1"))
    assert result["disk_path"] == "rbd:volumes/vol-1"
    assert 'protocol="rbd" name="volumes/vol-1"' in spawn_env[0]


def test_a_missing_local_root_volume_is_still_an_error(spawn_env, tmp_path):
    with pytest.raises(RuntimeError, match="root volume path does not exist"):
        worker.spawn_server("srv-2", spawn_payload(str(tmp_path / "gone.qcow2")))


class FailingRpc:
    def call(self, *args, **kwargs):
        pytest.fail("an RBD volume is reached over the Ceph network, never exported over iSCSI")


def test_an_rbd_volume_is_never_treated_as_remote(monkeypatch):
    monkeypatch.setattr(volume_manager, "COMPUTE_HOST", "compute01")
    volume = {"volume_id": "v1", "path": "rbd:volumes/vol-1", "host": "infra01"}
    assert volume_manager.connect(volume, FailingRpc()) == Path("rbd:volumes/vol-1")


def test_an_rbd_volume_can_be_attached(monkeypatch, ceph_conf, cephx):
    attached = []
    monkeypatch.setattr(volume_manager, "COMPUTE_HOST", "compute01")
    monkeypatch.setattr(volume_manager.libvirt_driver, "attach_disk", lambda dom, path, target, fmt: attached.append((dom, str(path), target, fmt)))
    payload = {"libvirt_domain_name": "dom", "volume": {"volume_id": "v1", "path": "rbd:volumes/vol-1", "host": "infra01", "target_dev": "sdb"}}

    result = volume_manager.attach_volume(payload, FailingRpc())

    assert attached == [("dom", "rbd:volumes/vol-1", "sdb", "raw")]
    assert result["path"] == "rbd:volumes/vol-1"
