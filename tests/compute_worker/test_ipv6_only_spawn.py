"""The server record's ip_address is overwritten with what spawn_server returns, so an IPv6-only port must come back
from the worker too, not only from compute-api."""
import pytest

pytest.importorskip("fastapi")

from services.compute_worker.app import worker  # noqa: E402


@pytest.fixture
def spawned(tmp_path, monkeypatch):
    seed, disk = tmp_path / "seed.iso", tmp_path / "root.qcow2"
    seed.write_bytes(b"")
    disk.write_bytes(b"")
    seen = {}
    monkeypatch.setattr(worker, "ensure_host_ready", lambda: None)
    monkeypatch.setattr(worker, "INSTANCE_DIR", tmp_path / "instances")
    monkeypatch.setattr(worker.cloudinit, "create_config_drive", lambda *a, **k: seen.setdefault("port", a[4]) and seed)
    monkeypatch.setattr(worker.network_manager, "create_tap", lambda tap: None)
    monkeypatch.setattr(worker.network_manager, "bind_tap_to_ovs", lambda *a, **k: None)
    monkeypatch.setattr(worker.libvirt_driver, "define_and_start", lambda xml: None)

    def run(port):
        payload = {"name": "vm1", "flavor_snapshot": {"vcpus": 1, "ram_mb": 512, "disk_gb": 1},
                   "root_volume": {"path": str(disk)}, "port": {"logical_port_name": "lp1", "mac_address": "02:00:00:00:00:01", **port}}
        return worker.spawn_server("srv-1", payload), seen

    return run


def test_an_ipv6_only_port_is_reported_back_as_the_servers_address(spawned):
    result, seen = spawned({"ipv6_address": "fd42:7:7::2", "ipv6_cidr": "fd42:7:7::/64"})
    assert result["ip_address"] == "fd42:7:7::2"
    assert seen["port"]["ipv6_cidr"] == "fd42:7:7::/64"      # the config drive gets the whole port


def test_ipv4_still_wins_on_a_dual_stack_port(spawned):
    result, _ = spawned({"fixed_ip": "10.0.0.5", "ipv6_address": "fd42:7:7::2"})
    assert result["ip_address"] == "10.0.0.5"
