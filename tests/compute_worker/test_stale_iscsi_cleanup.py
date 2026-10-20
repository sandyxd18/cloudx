import pytest

pytest.importorskip("fastapi")
from services.compute_worker.app import libvirt_driver, main  # noqa: E402

XML = """<domain><devices>
<disk type='block' device='disk'><source dev='/dev/disk/by-path/ip-10.0.0.5:3260-iscsi-iqn.2026-10.io.cloudx:vol-abc-lun-0'/></disk>
<disk type='block' device='disk'><source dev='/dev/vg-cloudx/vol-local'/></disk>
<disk type='file' device='cdrom'><source file='/var/lib/seed.iso'/></disk>
</devices></domain>"""


def test_iscsi_sessions_come_only_from_by_path_disks():
    assert libvirt_driver.iscsi_sessions_from_xml(XML) == [("10.0.0.5:3260", "iqn.2026-10.io.cloudx:vol-abc")]


def test_stale_domain_gets_its_iscsi_session_logged_out_after_delete(monkeypatch):
    order = []
    monkeypatch.setattr(main.libvirt_driver, "get_all_domain_states", lambda: {"moved": "RUNNING", "mine": "RUNNING"})
    monkeypatch.setattr(main.libvirt_driver, "iscsi_sessions", lambda n: [("p:3260", f"iqn:{n}")])
    monkeypatch.setattr(main.libvirt_driver, "delete", lambda n: order.append(("delete", n)))
    monkeypatch.setattr(main.iscsi, "logout", lambda p, t: order.append(("logout", t)))

    class R:
        status_code = 200
        def json(self):
            return {"servers": [{"host": "other", "libvirt_domain_name": "moved"}, {"host": main.COMPUTE_HOST, "libvirt_domain_name": "mine"}]}
    monkeypatch.setattr(main.requests, "get", lambda *a, **k: R())
    main.reconcile_stale_domains()
    assert order == [("delete", "moved"), ("logout", "iqn:moved")]


def test_a_failed_logout_does_not_stop_the_next_domain(monkeypatch):
    done = []
    monkeypatch.setattr(main.libvirt_driver, "get_all_domain_states", lambda: {"a": "RUNNING", "b": "RUNNING"})
    monkeypatch.setattr(main.libvirt_driver, "iscsi_sessions", lambda n: [("p:3260", n)])
    monkeypatch.setattr(main.libvirt_driver, "delete", lambda n: done.append(n))

    def boom(p, t):
        raise RuntimeError("busy")
    monkeypatch.setattr(main.iscsi, "logout", boom)

    class R:
        status_code = 200
        def json(self):
            return {"servers": [{"host": "x", "libvirt_domain_name": "a"}, {"host": "x", "libvirt_domain_name": "b"}]}
    monkeypatch.setattr(main.requests, "get", lambda *a, **k: R())
    main.reconcile_stale_domains()
    assert done == ["a", "b"]
