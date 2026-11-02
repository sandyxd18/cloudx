"""A hard reboot power-cycles the domain and starts it again from the definition libvirt holds. That definition is the one
that has the NICs and disks added after the server was created (attach_interface and attach_disk write to the persistent
config); the domain.xml saved at creation has not, so redefining from it dropped them: the record still listed the port,
the guest did not have it."""
import pytest

pytest.importorskip("fastapi")

from services.compute_worker.app import libvirt_driver, worker  # noqa: E402

DOMAIN_XML = """
<domain type="kvm">
  <name>mini-os-vm-12345678</name>
  <devices>
    <interface type="ethernet"><target dev="tap-mini-aaaaaa" managed="no"/></interface>
    <interface type="ethernet"><target dev="tap-mini-bbbbbb" managed="no"/></interface>
  </devices>
</domain>
"""


class FakeDomain:
    def __init__(self, events, active=True, saved_image=False):
        self.events, self._active, self._saved = events, active, saved_image

    def isActive(self):
        return self._active

    def XMLDesc(self, flags=0):
        return DOMAIN_XML

    def destroy(self):
        self._active = False
        self.events.append("destroy")

    def hasManagedSaveImage(self, flags=0):
        return self._saved

    def managedSaveRemove(self, flags=0):
        self._saved = False
        self.events.append("managed-save-removed")

    def create(self):
        self._active = True
        self.events.append("create")

    def undefineFlags(self, flags):
        self.events.append("undefine")

    def undefine(self):
        self.events.append("undefine")


class FakeConn:
    def __init__(self, domain, events):
        self._domain, self._events = domain, events

    def lookupByName(self, name):
        if self._domain is None:
            raise RuntimeError(f"Domain not found: no domain with matching name '{name}'")
        return self._domain

    def defineXML(self, xml):
        self._events.append("define")
        return self._domain

    def close(self):
        return None


@pytest.fixture
def host(monkeypatch):
    events = []

    def use(domain):
        monkeypatch.setattr(libvirt_driver, "connect", lambda: FakeConn(domain, events))

    monkeypatch.setattr(libvirt_driver.network_manager, "ensure_tap", lambda tap: events.append(f"tap:{tap}"))
    return events, use


def test_a_running_domain_is_stopped_and_started_again_without_being_undefined(host):
    events, use = host
    use(FakeDomain(events, active=True))

    assert libvirt_driver.hard_reboot("mini-os-vm-12345678") is True

    assert "undefine" not in events and "define" not in events, "the definition holds the NICs added after creation"
    assert events.index("destroy") < events.index("create")


def test_every_unmanaged_tap_exists_before_the_domain_is_created(host):
    events, use = host
    use(FakeDomain(events, active=True))

    libvirt_driver.hard_reboot("mini-os-vm-12345678")

    assert {"tap:tap-mini-aaaaaa", "tap:tap-mini-bbbbbb"} <= set(events)
    assert max(events.index("tap:tap-mini-aaaaaa"), events.index("tap:tap-mini-bbbbbb")) < events.index("create")


def test_a_stopped_domain_is_started_without_a_destroy(host):
    events, use = host
    use(FakeDomain(events, active=False))

    assert libvirt_driver.hard_reboot("mini-os-vm-12345678") is True

    assert "destroy" not in events
    assert "create" in events


def test_a_managed_save_image_is_dropped_so_the_domain_boots_instead_of_resuming(host):
    events, use = host
    use(FakeDomain(events, active=False, saved_image=True))

    libvirt_driver.hard_reboot("mini-os-vm-12345678")

    assert events.index("managed-save-removed") < events.index("create")


def test_a_domain_libvirt_does_not_know_is_reported_and_nothing_is_started(host):
    events, use = host
    use(None)

    assert libvirt_driver.hard_reboot("mini-os-vm-12345678") is False

    assert "create" not in events


def _worker_payload(tmp_path):
    return {"libvirt_domain_name": "mini-os-vm-12345678", "reboot_type": "hard", "instance_dir": str(tmp_path)}


def test_the_worker_hard_reboot_leaves_the_saved_xml_alone_when_libvirt_has_the_domain(monkeypatch, tmp_path):
    (tmp_path / "domain.xml").write_text("<domain>saved at creation</domain>")
    calls = []
    monkeypatch.setattr(worker.libvirt_driver, "hard_reboot", lambda name: calls.append(("hard_reboot", name)) or True)
    monkeypatch.setattr(worker.libvirt_driver, "delete", lambda name: calls.append(("delete", name)))
    monkeypatch.setattr(worker.libvirt_driver, "define_and_start", lambda xml: calls.append(("define", xml)))

    assert worker.reboot_server(_worker_payload(tmp_path)) == {"status": "ACTIVE"}

    assert calls == [("hard_reboot", "mini-os-vm-12345678")]


def test_the_worker_falls_back_to_the_saved_xml_when_libvirt_lost_the_domain(monkeypatch, tmp_path):
    (tmp_path / "domain.xml").write_text("<domain>saved at creation</domain>")
    calls = []
    monkeypatch.setattr(worker.libvirt_driver, "hard_reboot", lambda name: False)
    monkeypatch.setattr(worker.libvirt_driver, "delete", lambda name: calls.append(("delete", name)))
    monkeypatch.setattr(worker.libvirt_driver, "define_and_start", lambda xml: calls.append(("define", xml)))

    assert worker.reboot_server(_worker_payload(tmp_path)) == {"status": "ACTIVE"}

    assert ("define", "<domain>saved at creation</domain>") in calls


def test_a_soft_reboot_is_unchanged(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(worker.libvirt_driver, "reboot", lambda name: calls.append(("reboot", name)))
    monkeypatch.setattr(worker.libvirt_driver, "hard_reboot", lambda name: calls.append(("hard_reboot", name)))

    worker.reboot_server({"libvirt_domain_name": "mini-os-vm-12345678", "reboot_type": "soft"})

    assert calls == [("reboot", "mini-os-vm-12345678")]
