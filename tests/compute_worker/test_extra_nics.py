"""A server created on several networks gets one NIC per port, not only the primary one."""
import pytest

pytest.importorskip("fastapi")

from services.compute_worker.app import cloudinit, libvirt_driver, network_manager, worker  # noqa: E402

PRIMARY = {"id": "a1b2c3d4-primary", "logical_port_name": "lp-main", "mac_address": "02:00:00:00:00:01",
           "fixed_ip": "10.10.0.5", "cidr": "10.10.0.0/24", "gateway_ip": "10.10.0.1", "dns_nameservers": ["1.1.1.1"]}
EXTRA = {"id": "e5f6a7b8-extra", "logical_port_name": "lp-extra", "mac_address": "02:00:00:00:00:02",
         "ipv6_address": "fd42:7:7::7", "ipv6_cidr": "fd42:7:7::/64", "ipv6_gateway_ip": "fd42:7:7::1"}


@pytest.fixture
def host(tmp_path, monkeypatch):
    seed, disk = tmp_path / "seed.iso", tmp_path / "root.qcow2"
    seed.write_bytes(b"")
    disk.write_bytes(b"")
    seen = {"created": [], "bound": [], "deleted": [], "xml": None, "drive": {}}
    monkeypatch.setattr(worker, "ensure_host_ready", lambda: None)
    monkeypatch.setattr(worker, "INSTANCE_DIR", tmp_path / "instances")

    def drive(*args, **kwargs):
        seen["drive"] = {"args": args, "kwargs": kwargs}
        return seed

    monkeypatch.setattr(worker.cloudinit, "create_config_drive", drive)
    monkeypatch.setattr(worker.network_manager, "create_tap", lambda tap: seen["created"].append(tap))
    monkeypatch.setattr(worker.network_manager, "bind_tap_to_ovs", lambda tap, lport, mac, sid: seen["bound"].append((tap, lport, mac)))
    monkeypatch.setattr(worker.network_manager, "delete_tap", lambda tap: seen["deleted"].append(tap))
    monkeypatch.setattr(worker.libvirt_driver, "delete", lambda name: None)

    def define(xml):
        seen["xml"] = xml
        if seen.get("fail"):
            raise RuntimeError("libvirt refused")

    monkeypatch.setattr(worker.libvirt_driver, "define_and_start", define)

    def spawn(ports):
        payload = {"name": "vm1", "flavor_snapshot": {"vcpus": 1, "ram_mb": 512, "disk_gb": 1},
                   "root_volume": {"path": str(disk)}, "port": ports[0], "ports": ports}
        return worker.spawn_server("srv-1", payload)

    return spawn, seen


def test_every_port_of_a_new_server_gets_a_tap_bound_to_its_logical_port(host):
    spawn, seen = host
    spawn([PRIMARY, EXTRA])
    extra_tap = network_manager.port_tap_name("srv-1", "e5f6a7b8-extra")
    assert extra_tap in seen["created"]
    assert (extra_tap, "lp-extra", "02:00:00:00:00:02") in seen["bound"]
    assert len(seen["bound"]) == 2


def test_the_domain_has_a_nic_for_each_port(host):
    spawn, seen = host
    spawn([PRIMARY, EXTRA])
    extra_tap = network_manager.port_tap_name("srv-1", "e5f6a7b8-extra")
    assert seen["xml"].count("<interface") == 2
    assert f'dev="{extra_tap}"' in seen["xml"] and 'address="02:00:00:00:00:02"' in seen["xml"]


def test_a_server_with_one_port_is_built_as_before(host):
    spawn, seen = host
    spawn([PRIMARY])
    assert seen["xml"].count("<interface") == 1 and len(seen["created"]) == 1


def test_the_config_drive_is_told_about_the_extra_ports(host):
    spawn, seen = host
    spawn([PRIMARY, EXTRA])
    assert seen["drive"]["kwargs"]["extra_ports"] == [EXTRA]


def test_a_failed_build_removes_the_extra_taps_too(host):
    spawn, seen = host
    seen["fail"] = True
    with pytest.raises(RuntimeError):
        spawn([PRIMARY, EXTRA])
    assert network_manager.port_tap_name("srv-1", "e5f6a7b8-extra") in seen["deleted"]


def test_deleting_a_server_removes_the_taps_of_its_extra_ports(host):
    _, seen = host
    worker.delete_server("srv-1", {"libvirt_domain_name": "d1", "tap_name": "tap-main", "extra_port_ids": "e5f6a7b8-extra,c9d0e1f2-other"})
    assert {"tap-main", network_manager.port_tap_name("srv-1", "e5f6a7b8-extra"),
            network_manager.port_tap_name("srv-1", "c9d0e1f2-other")} <= set(seen["deleted"])


def test_domain_xml_adds_the_extra_interfaces():
    xml = libvirt_driver.domain_xml("vm", 512, 1, "/i/d.qcow2", "/i/seed.iso", "tap0", "02:00:00:00:00:01",
                                    extra_interfaces=[("tap1", "02:00:00:00:00:02")])
    assert xml.count("<interface") == 2 and 'dev="tap1" managed="no"' in xml


def test_network_config_configures_each_extra_nic_by_mac_without_a_second_default_route():
    text = cloudinit.render_network_config(PRIMARY, [EXTRA])
    assert 'match: {macaddress: "02:00:00:00:00:02"}' in text and "set-name: eth1" in text
    assert "addresses: [fd42:7:7::7/64]" in text
    assert "gateway6" not in text and text.count("gateway4") == 1      # only the primary port carries the default route


def test_an_extra_nic_without_an_address_asks_dhcp():
    text = cloudinit.render_network_config(PRIMARY, [{"id": "p3", "mac_address": "02:00:00:00:00:03"}])
    assert 'match: {macaddress: "02:00:00:00:00:03"}' in text and "set-name: eth1" in text and "dhcp4: true" in text
