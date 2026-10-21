import re
from pathlib import Path

import pytest

from cloudx_deb.manifest import ManifestError, Unit, load_manifest

ROOT = Path(__file__).resolve().parents[2]
SYSTEMD = ROOT / "etc" / "systemd"


@pytest.fixture(scope="module")
def units():
    return load_manifest()


def _by_name(units):
    return {u.name: u for u in units}


def test_every_systemd_unit_has_one_manifest_entry(units):
    from_manifest = sorted(u.unit_file for u in units)
    on_disk = sorted(p.name for p in SYSTEMD.glob("*.service"))
    assert from_manifest == on_disk


def test_api_fields_match_the_current_unit(units):
    apis = [u for u in units if u.kind == "api"]
    assert apis
    for u in apis:
        text = (SYSTEMD / u.unit_file).read_text()
        wd = re.search(r"^WorkingDirectory=(\S+)", text, re.M).group(1)
        assert u.dir == Path(wd).name, u.name
        port = re.search(r"--bind 0\.0\.0\.0:(\d+)|--port (\d+)", text)
        assert u.port == int(port.group(1) or port.group(2)), u.name
        w = re.search(r" -w (\d+)", text)
        assert u.workers == (int(w.group(1)) if w else 1), u.name


def test_daemon_dirs_match_the_current_unit(units):
    daemons = [u for u in units if u.kind == "daemon"]
    assert daemons
    for u in daemons:
        text = (SYSTEMD / u.unit_file).read_text()
        wd = re.search(r"^WorkingDirectory=(\S+)", text, re.M).group(1)
        assert u.dir == Path(wd).name, u.name
        assert u.port is None and u.workers is None and u.frontend is None, u.name


def test_names_and_ports_are_unique(units):
    names = [u.name for u in units]
    assert len(names) == len(set(names))
    ports = [u.port for u in units if u.port is not None]
    assert len(ports) == len(set(ports))


def test_frontend_values(units):
    assert {u.frontend for u in units if u.kind == "api"} <= {"nginx", "direct"}
    by = _by_name(units)
    for name in ("object-store-api", "metadata-api", "metadata-proxy", "dashboard"):
        assert by[name].frontend == "direct", name


def test_catalog_and_streaming(units):
    by = _by_name(units)
    assert by["image-api"].catalog == "image"
    assert by["auth-api"].catalog == "identity"
    assert by["object-store-api"].catalog == "object-store"
    assert by["dashboard"].catalog is None
    assert by["metadata-proxy"].catalog is None
    assert [u.name for u in units if u.streaming] == ["image-api"]


def test_bind_units_are_bundled_with_dns_worker(units):
    by = _by_name(units)
    for name in ("named-auth", "named-resolver"):
        assert by[name].kind == "unit"
        assert by[name].bundle_with == "dns-worker"
    assert by["dns-worker"].depends == ("bind9",)


def test_package_and_unit_file_names(units):
    u = _by_name(units)["compute-worker"]
    assert u.package == "cloudx-compute-worker"
    assert u.unit_file == "cloudx-compute-worker.service"


def _role(units, role):
    return {u.name for u in units if role in u.roles}


def test_roles(units):
    apis = {u.name for u in units if u.name.endswith("-api")} | {"dashboard"}
    assert _role(units, "controller") == apis | {
        "conductor", "reaper", "image-worker", "billing-worker", "event-worker",
        "orchestration-worker", "k8s-engine-worker", "dns-worker",
    }
    assert _role(units, "storage") == {"block-store-worker"}
    assert _role(units, "compute") == {
        "compute-worker", "metadata-proxy", "telemetry-worker", "lb-worker",
        "vpn-worker", "bgp-worker", "ha-watchdog",
    }
    by = _by_name(units)
    assert by["container-worker"].roles == ()
    assert by["file-store-worker"].roles == ()


def _write(tmp_path, body):
    p = tmp_path / "manifest.toml"
    p.write_text(body)
    return p


def _entry(name="x", kind="daemon", extra=""):
    return f'[[unit]]\nname = "{name}"\nkind = "{kind}"\n{extra}\n'


def test_load_returns_frozen_units(tmp_path):
    units = load_manifest(_write(tmp_path, _entry(extra='dir = "x"')))
    assert units == [Unit(name="x", kind="daemon", dir="x", port=None, workers=None,
                          frontend=None, streaming=False, catalog=None, roles=(),
                          depends=(), bundle_with=None)]
    with pytest.raises(AttributeError):
        units[0].name = "y"


def test_bad_manifest_raises(tmp_path):
    with pytest.raises(ManifestError):
        load_manifest(_write(tmp_path, _entry("a") + _entry("a")))
    api = 'dir = "x"\nport = 1\nworkers = 1\n'
    with pytest.raises(ManifestError):
        load_manifest(_write(tmp_path, _entry("a", "api", api + 'frontend = "apache"')))
    with pytest.raises(ManifestError):
        load_manifest(_write(tmp_path, _entry("a", "service")))
    with pytest.raises(ManifestError):
        load_manifest(_write(tmp_path, _entry("a", "api", 'dir = "x"')))
    with pytest.raises(ManifestError):
        load_manifest(_write(tmp_path, _entry("a", extra='bogus = 1')))
    with pytest.raises(ManifestError):
        load_manifest(_write(tmp_path, _entry("a", extra='roles = ["mainframe"]')))
