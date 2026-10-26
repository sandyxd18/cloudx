import os
import shutil
import subprocess
from collections import Counter
from pathlib import Path

import pytest

from cloudx_deb.build import MAINTAINER, build_all, control_file, relocate, strip_default_secret
from cloudx_deb.manifest import load_manifest
from cloudx_deb.render import render_api_unit, render_nginx, render_wrapper

ROOT = Path(__file__).resolve().parents[2]
VERSION = (ROOT / "VERSION").read_text().strip()
DEB_VERSION = f"{VERSION}-1"

pytestmark = pytest.mark.skipif(
    not (shutil.which("dpkg-deb") and shutil.which("fakeroot")), reason="needs dpkg-deb and fakeroot")


def dpkg_deb(*args) -> str:
    return subprocess.run(["dpkg-deb", *args], check=True, capture_output=True, text=True).stdout


def contents(deb: Path) -> dict[str, str]:
    """Path -> first column of `dpkg-deb --contents` (mode string), paths without the leading '.'."""
    out = {}
    for line in dpkg_deb("--contents", str(deb)).splitlines():
        fields = line.split(maxsplit=5)
        path = fields[5].split(" -> ")[0][1:]
        out[path] = fields[0]
    return out


def control_member(deb: Path, member: str, tmp_path: Path) -> str:
    dest = tmp_path / f"ctrl-{deb.stem}"
    if not dest.exists():
        dpkg_deb("--control", str(deb), str(dest))
    return (dest / member).read_text()


@pytest.fixture(scope="module")
def debs(tmp_path_factory) -> dict[str, Path]:
    base = tmp_path_factory.mktemp("deb")
    built = build_all(base / "out", VERSION, with_venv=False, work_dir=base / "work")
    return {p.name.split("_")[0]: p for p in built}


def test_control_file():
    text = control_file("cloudx-x", "1.0-1", ["a", "b (>= 2)"], "CloudX x")
    assert text.endswith("\n")
    fields = dict(line.split(": ", 1) for line in text.splitlines())
    assert fields == {
        "Package": "cloudx-x", "Version": "1.0-1", "Architecture": "amd64", "Maintainer": MAINTAINER,
        "Section": "admin", "Priority": "optional", "Homepage": "https://github.com/sandyxd18/cloudx",
        "Depends": "a, b (>= 2)", "Description": "CloudX x",
    }


def test_strip_default_secret():
    text = "[DEFAULT]\nauth_secret_key = cloudx-secret-key-change-in-production\nauth_secret_key = real\nx = 1\n"
    assert strip_default_secret(text) == "[DEFAULT]\nauth_secret_key = real\nx = 1\n"


def test_relocate_rewrites_the_staging_prefix_and_pip_trampolines():
    staging = b"/very/long/staging/opt/cloudx/venv"
    tramp = b"#!/bin/sh\n'''exec' " + staging + b"/bin/python \"$0\" \"$@\"\n' '''\n# -*- coding: utf-8 -*-\nimport re\n"
    assert relocate(tramp, staging) == b"#!/opt/cloudx/venv/bin/python\n# -*- coding: utf-8 -*-\nimport re\n"
    plain = b"#!" + staging + b"/bin/python\nimport re\n"
    assert relocate(plain, staging) == b"#!/opt/cloudx/venv/bin/python\nimport re\n"
    activate = b'VIRTUAL_ENV="' + staging + b'"\n'
    assert relocate(activate, staging) == b'VIRTUAL_ENV="/opt/cloudx/venv"\n'


def test_one_package_per_api_and_daemon_plus_common_db_meta_and_cli(debs):
    expected = {u.package for u in load_manifest() if u.kind != "unit"} | {
        "cloudx-common", "cloudx-db", "cloudx-controller", "cloudx-compute", "cloudx-storage", "cloudx-cli"}
    assert len(expected) == 47
    assert set(debs) == expected


def test_only_builds_the_named_packages(tmp_path):
    built = build_all(tmp_path / "out", VERSION, with_venv=False, only=["db", "image-api"], work_dir=tmp_path / "w")
    assert sorted(p.name for p in built) == [f"cloudx-db_{DEB_VERSION}_amd64.deb",
                                              f"cloudx-image-api_{DEB_VERSION}_amd64.deb"]
    with pytest.raises(ValueError, match="named-auth"):
        build_all(tmp_path / "out", VERSION, with_venv=False, only=["named-auth"], work_dir=tmp_path / "w")


def test_packages_have_the_expected_names_and_versions(debs):
    deb = debs["cloudx-image-api"]
    assert deb.name == f"cloudx-image-api_{DEB_VERSION}_amd64.deb"
    assert dpkg_deb("--field", str(deb), "Package").strip() == "cloudx-image-api"
    assert dpkg_deb("--field", str(deb), "Version").strip() == DEB_VERSION
    assert dpkg_deb("--field", str(deb), "Architecture").strip() == "amd64"


def test_unit_package_contents(debs):
    files = contents(debs["cloudx-image-api"])
    for path in ("/lib/systemd/system/cloudx-image-api.service", "/usr/bin/cloudx-image-api",
                 "/opt/cloudx/services/image/app/main.py", "/etc/cloudx/image-api/image-api.conf"):
        assert path in files
    assert not any(p.startswith("/etc/systemd/system/") for p in files)
    for part in ("__pycache__", "tests", "migrations"):
        assert not any(part in p.split("/") for p in files), part
    assert not any(p.endswith(".pyc") for p in files)


def test_unit_files_and_wrapper_come_from_the_renderers(debs, tmp_path):
    image = {u.name: u for u in load_manifest()}["image-api"]
    dpkg_deb("-x", str(debs["cloudx-image-api"]), str(tmp_path))
    wrapper = tmp_path / "usr/bin/cloudx-image-api"
    assert wrapper.read_text() == render_wrapper(image)
    assert wrapper.stat().st_mode & 0o777 == 0o755
    source = (ROOT / "etc/systemd/cloudx-image-api.service").read_text()
    assert (tmp_path / "lib/systemd/system/cloudx-image-api.service").read_text() == render_api_unit(image, source)
    conf = tmp_path / "etc/cloudx/image-api/image-api.conf"
    assert conf.read_text() == (ROOT / "etc/cloudx/image-api/image-api.conf.sample").read_text()
    daemon = tmp_path / "daemon"
    dpkg_deb("-x", str(debs["cloudx-compute-worker"]), str(daemon))
    assert "ExecStart=/usr/bin/cloudx-compute-worker\n" in (
        daemon / "lib/systemd/system/cloudx-compute-worker.service").read_text()
    assert (daemon / "etc/cloudx/compute-worker/compute-worker.conf").read_text() == "[DEFAULT]\n"


def test_file_modes(debs):
    files = contents(debs["cloudx-image-api"])
    assert files["/usr/bin/cloudx-image-api"] == "-rwxr-xr-x"
    assert files["/opt/cloudx/services/image/app/main.py"] == "-rw-r--r--"
    assert files["/opt/cloudx/services/image/app/"] == "drwxr-xr-x"


def test_bind_units_ship_verbatim_in_dns_worker(debs, tmp_path):
    dpkg_deb("-x", str(debs["cloudx-dns-worker"]), str(tmp_path))
    for name in ("cloudx-named-auth.service", "cloudx-named-resolver.service"):
        shipped = tmp_path / "lib/systemd/system" / name
        assert shipped.read_text() == (ROOT / "etc/systemd" / name).read_text()
        assert not (tmp_path / "usr/bin" / name.removesuffix(".service")).exists()
    postinst = control_member(debs["cloudx-dns-worker"], "postinst", tmp_path)
    assert "'cloudx-dns-worker.service'" in postinst
    assert "cloudx-named" not in postinst


def test_conffiles_are_declared(debs, tmp_path):
    conffiles = control_member(debs["cloudx-image-api"], "conffiles", tmp_path).splitlines()
    assert "/etc/cloudx/image-api/image-api.conf" in conffiles


def test_depends(debs):
    image = dpkg_deb("--field", str(debs["cloudx-image-api"]), "Depends")
    assert f"cloudx-common (= {DEB_VERSION})" in image
    assert "init-system-helpers" in image
    assert "python3 (>= 3.12), python3 (<< 3.13)" in dpkg_deb("--field", str(debs["cloudx-common"]), "Depends")
    assert "bind9" in dpkg_deb("--field", str(debs["cloudx-dns-worker"]), "Depends")
    assert f"cloudx-common (= {DEB_VERSION})" in dpkg_deb("--field", str(debs["cloudx-db"]), "Depends")


def test_db_package_owns_all_migrations_and_units_do_not(debs):
    db = contents(debs["cloudx-db"])
    assert "/opt/cloudx/services/migrations/registry.py" in db
    assert "/opt/cloudx/services/image/migrations/" in db
    for mig in (ROOT / "services").glob("*/migrations"):
        prefix = f"/opt/cloudx/services/{mig.parent.name}/migrations/"
        assert any(p.startswith(prefix) and not p.endswith("/") for p in db), mig


def test_every_package_ships_a_machine_readable_mit_copyright_file(debs, tmp_path):
    license_text = (ROOT / "LICENSE").read_text()
    for pkg, deb in debs.items():
        assert contents(deb)[f"/usr/share/doc/{pkg}/copyright"].startswith("-rw-r--r--"), pkg
        dest = tmp_path / pkg
        dpkg_deb("-x", str(deb), str(dest))
        text = (dest / f"usr/share/doc/{pkg}/copyright").read_text()
        assert text.startswith("Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/\n"), pkg
        assert "License: Expat\n" in text, pkg
        # The license body is the repo's LICENSE minus its copyright line, indented as DEP-5 requires.
        body = license_text.split("\n\n", 2)[2].strip()
        assert all(f" {line}".rstrip() in text for line in body.splitlines() if line.strip()), pkg


def test_no_file_is_owned_by_two_packages(debs):
    owners = Counter()
    for deb in debs.values():
        owners.update(p for p, mode in contents(deb).items() if not mode.startswith("d"))
    assert [p for p, n in owners.items() if n > 1] == []


def test_common_package_contents(debs, tmp_path):
    files = contents(debs["cloudx-common"])
    links = {}
    for line in dpkg_deb("--contents", str(debs["cloudx-common"])).splitlines():
        if " -> " in line:
            path, target = line.split(maxsplit=5)[5].split(" -> ")
            links[path[1:]] = target
    assert links["/usr/bin/cloudx-manage"] == "/opt/cloudx/venv/bin/cloudx-manage"
    assert links["/usr/bin/cloudx-db-migrate"] == "/opt/cloudx/venv/bin/cloudx-db-migrate"
    assert "/usr/share/cloudx/cloudx.conf.sample" in files
    assert "/usr/share/cloudx/manifest.toml" in files
    assert not any(p.startswith("/opt/cloudx/venv") for p in files)
    dpkg_deb("-x", str(debs["cloudx-common"]), str(tmp_path / "x"))
    assert (tmp_path / "x/usr/lib/tmpfiles.d/cloudx.conf").read_text() == "d /run/cloudx 0755 cloudx cloudx -\n"


def test_postinst_enables_and_starts_via_systemd_helpers(debs, tmp_path):
    for pkg, deb in debs.items():
        control_member(deb, "control", tmp_path)
        for script in ("postinst", "prerm", "postrm"):
            path = tmp_path / f"ctrl-{deb.stem}" / script
            if path.exists():
                assert path.read_text().startswith("#!/bin/sh\nset -e\n"), (pkg, script)
                assert path.stat().st_mode & 0o111, (pkg, script)
                subprocess.run(["sh", "-n", str(path)], check=True)
    postinst = control_member(debs["cloudx-image-api"], "postinst", tmp_path)
    assert "deb-systemd-helper enable 'cloudx-image-api.service'" in postinst
    assert "deb-systemd-invoke $_action 'cloudx-image-api.service'" in postinst
    assert "[ -d /run/systemd/system ]" in postinst
    assert "deb-systemd-invoke stop 'cloudx-image-api.service'" in control_member(
        debs["cloudx-image-api"], "prerm", tmp_path)


def test_purging_an_nginx_api_reloads_nginx_but_other_units_leave_it_alone(debs, tmp_path):
    # dpkg deletes the snippet conffile on purge; nginx keeps serving the old route until it is reloaded.
    postrm = control_member(debs["cloudx-image-api"], "postrm", tmp_path)
    after_purge = postrm.split('if [ "$1" = purge ] && [ -d /run/systemd/system ]')[1]
    assert "nginx -t" in after_purge
    assert "deb-systemd-invoke reload nginx.service" in after_purge
    # a failed reload never fails the purge
    assert "|| true" in after_purge or "|| echo" in after_purge
    for pkg in ("cloudx-dashboard", "cloudx-metadata-api", "cloudx-conductor"):
        assert "nginx" not in control_member(debs[pkg], "postrm", tmp_path), pkg


def test_common_postinst(debs, tmp_path):
    postinst = control_member(debs["cloudx-common"], "postinst", tmp_path)
    assert '"$1" = "configure"' in postinst
    assert "useradd --system" in postinst
    assert "systemd-tmpfiles --create /usr/lib/tmpfiles.d/cloudx.conf" in postinst
    init = "cloudx-manage conf init --path /etc/cloudx/cloudx.conf --sample /usr/share/cloudx/cloudx.conf.sample"
    assert init in postinst
    after = postinst[postinst.index(init):]
    assert "chown root:cloudx /etc/cloudx/cloudx.conf" in after
    assert "chmod 640 /etc/cloudx/cloudx.conf" in after
    # Per-unit confs ship 0644 and may hold DB passwords: the directory is what keeps them private.
    assert "chown root:cloudx /etc/cloudx\n" in after
    assert "chmod 750 /etc/cloudx\n" in after
    for sub in ("images", "object-store", "compute", "dns", "ha-watchdog", "fs-state"):
        assert f"/var/lib/cloudx/{sub}" in postinst


def _units():
    return {u.name: u for u in load_manifest()}


def test_nginx_api_ships_its_snippet_as_a_conffile(debs, tmp_path):
    image = _units()["image-api"]
    deb = debs["cloudx-image-api"]
    snippet = "/etc/nginx/conf.d/cloudx-image-api.conf"
    assert contents(deb)[snippet] == "-rw-r--r--"
    assert snippet in control_member(deb, "conffiles", tmp_path).splitlines()
    assert "/etc/cloudx/image-api/image-api.conf" in control_member(deb, "conffiles", tmp_path).splitlines()
    dpkg_deb("-x", str(deb), str(tmp_path / "x"))
    assert (tmp_path / "x" / snippet.lstrip("/")).read_text() == render_nginx(image)
    depends = dpkg_deb("--field", str(deb), "Depends")
    assert "nginx" in [d.strip() for d in depends.split(",")]
    assert f"cloudx-common (= {DEB_VERSION})" in depends
    assert "init-system-helpers" in depends


def test_nginx_api_unit_and_wrapper_use_the_nginx_variant(debs, tmp_path):
    dpkg_deb("-x", str(debs["cloudx-image-api"]), str(tmp_path))
    unit = (tmp_path / "lib/systemd/system/cloudx-image-api.service").read_text()
    assert "ExecStart=/usr/bin/cloudx-image-api\n" in unit
    wrapper = (tmp_path / "usr/bin/cloudx-image-api").read_text()
    assert 'frontend="nginx"' in wrapper
    assert "port=8002" in wrapper
    assert "run_api(" in wrapper


def test_direct_api_has_no_snippet_and_no_nginx_dependency(debs, tmp_path):
    deb = debs["cloudx-object-store-api"]
    assert not any(p.startswith("/etc/nginx/") for p in contents(deb))
    assert not any("nginx" in line for line in control_member(deb, "conffiles", tmp_path).splitlines())
    depends = [d.strip() for d in dpkg_deb("--field", str(deb), "Depends").split(",")]
    assert "nginx" not in depends
    assert "nginx" not in control_member(deb, "postinst", tmp_path)


def test_every_nginx_api_ships_exactly_its_own_snippet(debs):
    for name, u in _units().items():
        if u.kind != "api":
            continue
        snippets = [p for p, mode in contents(debs[u.package]).items()
                    if p.startswith("/etc/nginx/") and not mode.startswith("d")]
        assert snippets == ([f"/etc/nginx/conf.d/cloudx-{name}.conf"] if u.frontend == "nginx" else []), name


def test_no_package_ships_an_nginx_systemd_drop_in(debs):
    for pkg, deb in debs.items():
        assert not any(p.startswith("/etc/systemd/system/nginx.service.d") for p in contents(deb)), pkg


def test_nginx_postinst_checks_config_then_reloads(debs, tmp_path):
    postinst = control_member(debs["cloudx-image-api"], "postinst", tmp_path)
    subprocess.run(["sh", "-n", str(tmp_path / f"ctrl-{debs['cloudx-image-api'].stem}" / "postinst")], check=True)
    own = postinst.index("deb-systemd-invoke $_action 'cloudx-image-api.service'")
    guard = postinst.index('if [ "$1" = "configure" ] && [ -d /run/systemd/system ] && command -v nginx', own)
    check = postinst.index("nginx -t", guard)
    reload = postinst.index("deb-systemd-invoke reload nginx.service", check)
    warn = postinst.index('echo "warning: nginx -t failed', check)
    assert own < guard < check < reload < warn
    assert postinst.index("else", reload) < warn
    assert postinst[warn:].split("\n", 1)[0].endswith(">&2")
    assert "|| echo" in postinst[reload:].split("\n", 1)[0]
    assert postinst.count("nginx.service") == 1
    for gone in ("restart nginx", ".nginx-group-applied", "touch ", "SupplementaryGroups"):
        assert gone not in postinst
    for deb in (debs["cloudx-object-store-api"], debs["cloudx-compute-worker"]):
        assert "nginx" not in control_member(deb, "postinst", tmp_path)


@pytest.mark.skipif(os.environ.get("CLOUDX_DEB_FULL_BUILD") != "1", reason="set CLOUDX_DEB_FULL_BUILD=1 (needs network)")
def test_full_build_relocates_the_venv(tmp_path):
    [deb] = build_all(tmp_path / "out", VERSION, only=["common"], work_dir=tmp_path / "work")
    dpkg_deb("-x", str(deb), str(tmp_path / "x"))
    venv_bin = tmp_path / "x/opt/cloudx/venv/bin"
    assert (venv_bin / "cloudx-manage").read_text().startswith("#!/opt/cloudx/venv/bin/python")
    for f in venv_bin.iterdir():
        if f.is_file() and not f.is_symlink():
            assert str(tmp_path) not in f.read_bytes().decode(errors="replace"), f
    assert str(tmp_path) not in (tmp_path / "x/opt/cloudx/venv/pyvenv.cfg").read_text()
