"""Low-level pieces of live migration: rewriting the disk sources of a domain XML for the destination node, the libvirt
migration call with its timeout, and the split of bind_tap_to_ovs into plugging the tap and writing its OVS identity."""
import importlib
import threading
import time
import xml.etree.ElementTree as ET
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")

from services.compute_worker.app import libvirt_driver, network_manager  # noqa: E402

SOURCE_ISCSI = "/dev/disk/by-path/ip-10.0.0.9:3260-iscsi-iqn.old-lun-1"
NEW_ISCSI = "/dev/disk/by-path/ip-1.2.3.4:3260-iscsi-iqn.x-lun-0"

DOMAIN_XML = f"""<domain type="kvm">
  <name>vm1</name>
  <devices>
    <disk type="block" device="disk"><driver name="qemu" type="raw"/><source dev="/dev/vg/v1"/><target dev="sdb" bus="scsi"/></disk>
    <disk type="block" device="disk"><driver name="qemu" type="raw"/><source dev="{SOURCE_ISCSI}"/><target dev="sdc" bus="scsi"/></disk>
    <disk type="file" device="cdrom"><driver name="qemu" type="raw"/><source file="/var/lib/cloudx/seed.iso"/><target dev="sda" bus="sata"/></disk>
    <disk type="network" device="disk"><driver name="qemu" type="raw"/><source protocol="rbd" name="vms/vm1_disk"><host name="10.0.0.1" port="6789"/></source><target dev="vda" bus="virtio"/></disk>
  </devices>
</domain>"""


def _sources(xml):
    return [ET.tostring(d.find("source"), encoding="unicode") for d in ET.fromstring(xml).findall("./devices/disk")]


def test_rewrite_changes_only_listed_block_and_file_sources():
    out = libvirt_driver.rewrite_disk_sources(DOMAIN_XML, {"/dev/vg/v1": NEW_ISCSI})

    before, after = _sources(DOMAIN_XML), _sources(out)
    assert ET.fromstring(out).find("./devices/disk/source").get("dev") == NEW_ISCSI
    assert after[1:] == before[1:]            # the iSCSI disk, the config drive and the RBD disk are untouched
    assert NEW_ISCSI not in "".join(after[1:])


def test_rewrite_also_replaces_a_file_source():
    out = libvirt_driver.rewrite_disk_sources(DOMAIN_XML, {"/var/lib/cloudx/seed.iso": "/mnt/seed.iso"})

    assert ET.fromstring(out).findall("./devices/disk/source")[2].get("file") == "/mnt/seed.iso"
    assert _sources(out)[:2] == _sources(DOMAIN_XML)[:2]


class FakeLibvirt:
    """A libvirt module with only what migrate_domain and domain_xml_for_migration read."""
    VIR_MIGRATE_LIVE = 1
    VIR_MIGRATE_PEER2PEER = 2
    VIR_MIGRATE_PERSIST_DEST = 8
    VIR_MIGRATE_UNDEFINE_SOURCE = 16
    VIR_MIGRATE_ABORT_ON_ERROR = 4096
    VIR_MIGRATE_AUTO_CONVERGE = 8192
    VIR_MIGRATE_PARAM_DEST_XML = "destination_xml"
    VIR_MIGRATE_PARAM_BANDWIDTH = "bandwidth"
    VIR_DOMAIN_XML_MIGRATABLE = 8


class FakeDomain:
    def __init__(self, migrate=None):
        self.calls, self.aborted, self.downtime = [], threading.Event(), None
        self.downtime_set = threading.Event()
        self._migrate = migrate

    def migrateToURI3(self, uri, params, flags):
        self.calls.append((uri, params, flags))
        if self._migrate:
            self._migrate(self)

    def migrateSetMaxDowntime(self, ms, flags=0):
        self.downtime = ms
        self.downtime_set.set()

    def jobInfo(self):
        return [1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]

    def abortJob(self):
        self.aborted.set()

    def XMLDesc(self, flags=0):
        return f"<domain flags='{flags}'/>"


class FakeConn:
    def __init__(self, domain):
        self.domain, self.closed = domain, False

    def lookupByName(self, name):
        return self.domain

    def close(self):
        self.closed = True


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setattr(libvirt_driver, "libvirt", FakeLibvirt)
    monkeypatch.setattr(libvirt_driver, "_MIGRATION_POLL_SECONDS", 0.05)

    def install(domain):
        conn = FakeConn(domain)
        monkeypatch.setattr(libvirt_driver, "connect", lambda: conn)
        return conn

    return install


def _migrate(domain, **overrides):
    opts = dict(timeout=30, bandwidth_mbps=0, max_downtime_ms=300, auto_converge=False)
    opts.update(overrides)
    libvirt_driver.migrate_domain("vm1", "qemu+ssh://root@node-2/system", "<domain>dest</domain>", **opts)


def test_migrate_domain_aborts_after_the_timeout(fake):
    def until_aborted(d):
        # blocks until abortJob, like a migration that never converges, then fails as libvirt does
        d.aborted.wait(10)
        raise RuntimeError("operation aborted: migration out: canceled by client")

    domain = FakeDomain(migrate=until_aborted)
    conn = fake(domain)

    started = time.monotonic()
    with pytest.raises(libvirt_driver.MigrationError, match="timed out after 1 s"):
        _migrate(domain, timeout=1)

    assert domain.aborted.is_set()
    assert time.monotonic() - started < 5
    assert conn.closed


def test_migrate_domain_succeeds_when_the_call_finishes_as_the_timeout_aborts(fake):
    # the call returns normally the moment abortJob arrives: the domain is already on the destination
    domain = FakeDomain(migrate=lambda d: d.aborted.wait(10))
    conn = fake(domain)

    _migrate(domain, timeout=1)       # does not raise

    assert domain.aborted.is_set()
    assert conn.closed


@pytest.mark.parametrize("mbps,expected", [(1, 1), (8, 1), (100, 12), (1000, 119)])
def test_bandwidth_is_converted_from_megabits_to_mib_per_second(fake, mbps, expected):
    domain = FakeDomain()
    fake(domain)

    _migrate(domain, bandwidth_mbps=mbps)

    assert domain.calls[0][1][FakeLibvirt.VIR_MIGRATE_PARAM_BANDWIDTH] == expected


def test_migrate_domain_passes_flags_params_and_options(fake):
    L = FakeLibvirt
    base = L.VIR_MIGRATE_LIVE | L.VIR_MIGRATE_PEER2PEER | L.VIR_MIGRATE_PERSIST_DEST | L.VIR_MIGRATE_UNDEFINE_SOURCE | L.VIR_MIGRATE_ABORT_ON_ERROR

    plain = FakeDomain()
    fake(plain)
    _migrate(plain)
    uri, params, flags = plain.calls[0]
    assert uri == "qemu+ssh://root@node-2/system"
    assert flags == base                                   # no auto-converge unless asked
    assert params == {L.VIR_MIGRATE_PARAM_DEST_XML: "<domain>dest</domain>"}   # no bandwidth when 0

    tuned = FakeDomain(migrate=lambda d: d.downtime_set.wait(5))
    fake(tuned)
    _migrate(tuned, auto_converge=True, bandwidth_mbps=100, max_downtime_ms=250)
    _, params, flags = tuned.calls[0]
    assert flags == base | L.VIR_MIGRATE_AUTO_CONVERGE
    assert params[L.VIR_MIGRATE_PARAM_BANDWIDTH] == 12      # 100 Mbit/s is 11.9 MiB/s, which libvirt takes in MiB/s
    assert params[L.VIR_MIGRATE_PARAM_DEST_XML] == "<domain>dest</domain>"
    assert tuned.downtime == 250                           # set once the migration is running


def test_migrate_domain_wraps_a_libvirt_error(fake):
    def boom(domain):
        raise RuntimeError("internal error: unable to connect to the destination")

    domain = FakeDomain(migrate=boom)
    conn = fake(domain)

    with pytest.raises(libvirt_driver.MigrationError, match="unable to connect to the destination"):
        _migrate(domain)
    assert conn.closed


def test_domain_xml_for_migration_asks_for_the_migratable_xml(fake):
    conn = fake(FakeDomain())

    assert libvirt_driver.domain_xml_for_migration("vm1") == f"<domain flags='{FakeLibvirt.VIR_DOMAIN_XML_MIGRATABLE}'/>"
    assert conn.closed


def test_migration_error_is_a_runtime_error():
    assert issubclass(libvirt_driver.MigrationError, RuntimeError)


@pytest.fixture
def commands(monkeypatch):
    seen = []
    monkeypatch.setattr(network_manager, "run", lambda cmd, **kwargs: seen.append(list(cmd)))
    return seen


def test_plug_tap_sets_no_iface_id(commands):
    network_manager.plug_tap("tap-mini-aaaaaa")

    assert commands == [["ovs-vsctl", "--may-exist", "add-port", network_manager.OVS_BRIDGE, "tap-mini-aaaaaa"]]


def test_set_tap_binding_writes_the_four_external_ids(commands):
    network_manager.set_tap_binding("tap-mini-aaaaaa", "lsp-1", "fa:16:3e:00:00:01", "srv-1")

    assert commands == [
        ["ovs-vsctl", "set", "Interface", "tap-mini-aaaaaa", "external_ids:iface-id=lsp-1"],
        ["ovs-vsctl", "set", "Interface", "tap-mini-aaaaaa", "external_ids:attached-mac=fa:16:3e:00:00:01"],
        ["ovs-vsctl", "set", "Interface", "tap-mini-aaaaaa", "external_ids:vm-id=srv-1"],
        ["ovs-vsctl", "set", "Interface", "tap-mini-aaaaaa", "external_ids:iface-status=active"],
    ]


def test_bind_tap_to_ovs_is_unchanged(commands):
    network_manager.bind_tap_to_ovs("tap-mini-aaaaaa", "lsp-1", "fa:16:3e:00:00:01", "srv-1")

    assert commands == [
        ["ovs-vsctl", "--may-exist", "add-port", network_manager.OVS_BRIDGE, "tap-mini-aaaaaa"],
        ["ovs-vsctl", "set", "Interface", "tap-mini-aaaaaa", "external_ids:iface-id=lsp-1"],
        ["ovs-vsctl", "set", "Interface", "tap-mini-aaaaaa", "external_ids:attached-mac=fa:16:3e:00:00:01"],
        ["ovs-vsctl", "set", "Interface", "tap-mini-aaaaaa", "external_ids:vm-id=srv-1"],
        ["ovs-vsctl", "set", "Interface", "tap-mini-aaaaaa", "external_ids:iface-status=active"],
    ]


@pytest.fixture
def reloaded_config(monkeypatch):
    from services.compute_worker.app import config

    yield lambda: importlib.reload(config)
    monkeypatch.undo()
    importlib.reload(config)


KEYS = ["LIVE_MIGRATION_HOST", "LIVE_MIGRATION_URI", "LIVE_MIGRATION_TIMEOUT", "LIVE_MIGRATION_MAX_DOWNTIME_MS",
        "LIVE_MIGRATION_BANDWIDTH_MBPS", "LIVE_MIGRATION_AUTO_CONVERGE"]


def test_live_migration_settings_default_and_override(monkeypatch, reloaded_config):
    for key in KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("COMPUTE_HOST", "node-9")
    cfg = reloaded_config()

    assert cfg.LIVE_MIGRATION_HOST == "node-9"
    assert cfg.LIVE_MIGRATION_URI == "qemu+ssh://root@{host}/system"
    assert (cfg.LIVE_MIGRATION_TIMEOUT, cfg.LIVE_MIGRATION_MAX_DOWNTIME_MS, cfg.LIVE_MIGRATION_BANDWIDTH_MBPS) == (1800, 300, 0)
    assert cfg.LIVE_MIGRATION_AUTO_CONVERGE is False

    monkeypatch.setenv("LIVE_MIGRATION_HOST", "10.1.1.2")
    monkeypatch.setenv("LIVE_MIGRATION_URI", "qemu+tcp://{host}/system")
    monkeypatch.setenv("LIVE_MIGRATION_TIMEOUT", "60")
    monkeypatch.setenv("LIVE_MIGRATION_MAX_DOWNTIME_MS", "500")
    monkeypatch.setenv("LIVE_MIGRATION_BANDWIDTH_MBPS", "200")
    monkeypatch.setenv("LIVE_MIGRATION_AUTO_CONVERGE", "Yes")
    cfg = reloaded_config()

    assert cfg.LIVE_MIGRATION_HOST == "10.1.1.2"
    assert cfg.LIVE_MIGRATION_URI == "qemu+tcp://{host}/system"
    assert (cfg.LIVE_MIGRATION_TIMEOUT, cfg.LIVE_MIGRATION_MAX_DOWNTIME_MS, cfg.LIVE_MIGRATION_BANDWIDTH_MBPS) == (60, 500, 200)
    assert cfg.LIVE_MIGRATION_AUTO_CONVERGE is True


@pytest.mark.parametrize("raw,expected", [("true", True), ("1", True), ("YES", True), ("false", False), ("0", False), ("", False)])
def test_auto_converge_parsing(monkeypatch, reloaded_config, raw, expected):
    monkeypatch.setenv("LIVE_MIGRATION_AUTO_CONVERGE", raw)

    assert reloaded_config().LIVE_MIGRATION_AUTO_CONVERGE is expected
