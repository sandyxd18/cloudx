import pytest

pytest.importorskip("fastapi")
from services.compute_worker.app import libvirt_driver, main  # noqa: E402

XML = """<domain><devices>
<interface type='ethernet'><mac address='02:1c:8a:40:0e:b8'/><target dev='tap-mini-21bc5b' managed='no'/></interface>
<interface type='ethernet'><mac address='02:20:09:f2:57:56'/><target dev='tap-mini-21b13b' managed='no'/></interface>
<interface type='bridge'><source bridge='br0'/><target dev='vnet7'/></interface>
<disk type='file' device='disk'><source file='/var/lib/disk.img'/></disk>
</devices></domain>"""


def test_taps_of_a_domain_are_its_unmanaged_ethernet_targets():
    assert libvirt_driver.interface_taps_from_xml(XML) == ["tap-mini-21bc5b", "tap-mini-21b13b"]


class _Servers:
    status_code = 200

    def __init__(self, *servers):
        self._servers = list(servers)

    def json(self):
        return {"servers": self._servers}


def _stale_domain_setup(monkeypatch, taps_of, order):
    monkeypatch.setattr(main.libvirt_driver, "get_all_domain_states", lambda: {"moved": "RUNNING", "mine": "RUNNING"})
    monkeypatch.setattr(main.libvirt_driver, "iscsi_sessions", lambda n: [])
    monkeypatch.setattr(main.libvirt_driver, "interface_taps", taps_of)
    monkeypatch.setattr(main.libvirt_driver, "delete", lambda n: order.append(("delete", n)))
    monkeypatch.setattr(main.network_manager, "delete_tap", lambda t: order.append(("tap", t)))
    monkeypatch.setenv("COMPUTE_API_URL", "http://compute.test:8003")
    servers = _Servers({"host": "other", "libvirt_domain_name": "moved"}, {"host": main.COMPUTE_HOST, "libvirt_domain_name": "mine"})
    monkeypatch.setattr(main.requests, "get", lambda *a, **k: servers)


def test_stale_domain_loses_its_taps_after_it_is_deleted(monkeypatch):
    """A tap left in OVS keeps its iface-id, so this node's ovn-controller and the new host's fight over the port."""
    order = []
    _stale_domain_setup(monkeypatch, lambda n: [f"{n}-tap0", f"{n}-tap1"], order)

    main.reconcile_stale_domains()

    assert order == [("delete", "moved"), ("tap", "moved-tap0"), ("tap", "moved-tap1")]  # "mine" is untouched


def test_unreadable_taps_do_not_stop_the_domain_delete(monkeypatch):
    order = []

    def unreadable(name):
        raise RuntimeError("libvirt gone")
    _stale_domain_setup(monkeypatch, unreadable, order)

    main.reconcile_stale_domains()

    assert order == [("delete", "moved")]


def test_a_failed_tap_delete_does_not_stop_the_next_tap(monkeypatch):
    order = []
    _stale_domain_setup(monkeypatch, lambda n: ["tap-a", "tap-b"], order)

    def flaky(tap):
        order.append(("tap", tap))
        if tap == "tap-a":
            raise RuntimeError("ovs-vsctl missing")
    monkeypatch.setattr(main.network_manager, "delete_tap", flaky)

    main.reconcile_stale_domains()

    assert order == [("delete", "moved"), ("tap", "tap-a"), ("tap", "tap-b")]
