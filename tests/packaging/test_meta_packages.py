import shutil
import subprocess
from pathlib import Path

import pytest

from cloudx_deb.build import MAINTAINER, build_all, relocate
from cloudx_deb.manifest import load_manifest

ROOT = Path(__file__).resolve().parents[2]
VERSION = (ROOT / "VERSION").read_text().strip()
DEB_VERSION = f"{VERSION}-1"

pytestmark = pytest.mark.skipif(
    not (shutil.which("dpkg-deb") and shutil.which("fakeroot")), reason="needs dpkg-deb and fakeroot")

COMPUTE_UNITS = ["compute-worker", "metadata-proxy", "telemetry-worker", "lb-worker", "vpn-worker", "bgp-worker",
                 "ha-watchdog"]


def field(deb: Path, name: str) -> str:
    return subprocess.run(["dpkg-deb", "--field", str(deb), name], check=True, capture_output=True,
                          text=True).stdout.strip()


def entries(deb: Path) -> dict[str, tuple[str, str, str | None]]:
    """Path -> (mode, owner, symlink target)."""
    out = subprocess.run(["dpkg-deb", "--contents", str(deb)], check=True, capture_output=True, text=True).stdout
    result = {}
    for line in out.splitlines():
        mode, owner, _size, _date, _time, rest = line.split(maxsplit=5)
        path, _, target = rest.partition(" -> ")
        result[path[1:]] = (mode, owner, target or None)
    return result


def names(value: str) -> list[str]:
    return [d.strip() for d in value.split(",")]


@pytest.fixture(scope="module")
def debs(tmp_path_factory) -> dict[str, Path]:
    base = tmp_path_factory.mktemp("meta")
    built = build_all(base / "out", VERSION, with_venv=False, work_dir=base / "work")
    return {p.name.split("_")[0]: p for p in built}


def test_a_default_build_yields_47_packages(debs):
    assert len(debs) == 47
    assert {"cloudx-controller", "cloudx-compute", "cloudx-storage", "cloudx-cli"} <= set(debs)


def test_only_accepts_the_meta_and_cli_names(tmp_path):
    built = build_all(tmp_path / "out", VERSION, with_venv=False,
                      only=["controller", "compute", "storage", "cli"], work_dir=tmp_path / "w")
    assert sorted(p.name.split("_")[0] for p in built) == [
        "cloudx-cli", "cloudx-compute", "cloudx-controller", "cloudx-storage"]


def test_compute_depends_on_exactly_the_seven_compute_units(debs):
    assert sorted(names(field(debs["cloudx-compute"], "Depends"))) == sorted(
        f"cloudx-{n} (= {DEB_VERSION})" for n in COMPUTE_UNITS)
    assert {u.name for u in load_manifest() if "compute" in u.roles} == set(COMPUTE_UNITS)


def test_meta_packages_keep_the_architecture_and_version_of_the_rest(debs):
    for pkg in ("cloudx-controller", "cloudx-compute", "cloudx-storage", "cloudx-cli"):
        assert field(debs[pkg], "Architecture") == "amd64"
        assert field(debs[pkg], "Version") == DEB_VERSION
        assert field(debs[pkg], "Maintainer") == MAINTAINER


def test_controller_depends_and_recommends(debs):
    depends = names(field(debs["cloudx-controller"], "Depends"))
    for pkg in ("cloudx-dashboard", "cloudx-conductor", "cloudx-db"):
        assert f"{pkg} (= {DEB_VERSION})" in depends
    assert not any(d.startswith("cloudx-dns-worker") or d == "bind9" for d in depends)
    assert all(d.startswith("cloudx-") and f"(= {DEB_VERSION})" in d for d in depends)
    expected = {f"cloudx-{u.name}" for u in load_manifest()
                if u.kind != "unit" and "controller" in u.roles and not u.depends}
    assert {d.split(" ")[0] for d in depends} == expected | {"cloudx-db"}
    recommends = names(field(debs["cloudx-controller"], "Recommends"))
    assert f"cloudx-dns-worker (= {DEB_VERSION})" in recommends
    assert "cloudx-cli" in recommends
    for apt in ("postgresql", "rabbitmq-server", "memcached", "ovn-central"):
        assert apt in recommends


def test_compute_recommends(debs):
    recommends = names(field(debs["cloudx-compute"], "Recommends"))
    assert sorted(recommends) == sorted(["cloudx-cli", "libvirt-daemon-system", "qemu-kvm", "openvswitch-switch",
                                         "ovn-host", "qemu-utils"])


def test_storage_depends_and_recommends(debs):
    assert names(field(debs["cloudx-storage"], "Depends")) == [f"cloudx-block-store-worker (= {DEB_VERSION})"]
    assert names(field(debs["cloudx-storage"], "Recommends")) == ["cloudx-cli"]


def shipped_files(deb) -> list[str]:
    """Files a package installs, without the copyright file every package carries under /usr/share/doc."""
    return sorted(p for p, (mode, _, _) in entries(deb).items()
                  if not mode.startswith("d") and not p.startswith("/usr/share/doc/"))


def test_meta_packages_have_no_maintainer_scripts(debs, tmp_path):
    for pkg in ("cloudx-controller", "cloudx-storage"):
        files = shipped_files(debs[pkg])
        assert files == ([] if pkg == "cloudx-controller" else ["/usr/sbin/cloudx-setup-storage"]), pkg
        dest = tmp_path / pkg
        subprocess.run(["dpkg-deb", "--control", str(debs[pkg]), str(dest)], check=True)
        assert sorted(p.name for p in dest.iterdir()) == ["control"]


def test_cli_depends_only_on_python_and_ships_the_command(debs):
    assert field(debs["cloudx-cli"], "Depends") == "python3 (>= 3.12), python3 (<< 3.13)"
    mode, owner, target = entries(debs["cloudx-cli"])["/usr/bin/cloudx"]
    assert mode.startswith("l")
    assert owner == "root/root"
    assert target == "/opt/cloudx/cli/venv/bin/cloudx"


def test_relocate_targets_another_venv():
    staging = b"/very/long/staging/opt/cloudx/cli/venv"
    tramp = b"#!/bin/sh\n'''exec' " + staging + b"/bin/python \"$0\" \"$@\"\n' '''\nimport re\n"
    assert relocate(tramp, staging, "/opt/cloudx/cli/venv") == b"#!/opt/cloudx/cli/venv/bin/python\nimport re\n"
    assert relocate(b"#!" + staging + b"/bin/python\n", staging, "/opt/cloudx/cli/venv") == \
        b"#!/opt/cloudx/cli/venv/bin/python\n"


@pytest.mark.parametrize("package,script,source", [
    ("cloudx-compute", "cloudx-setup-compute", "setup-compute-node.sh"),
    ("cloudx-compute", "cloudx-setup-ceph-secret", "setup-ceph-libvirt-secret.sh"),
    ("cloudx-storage", "cloudx-setup-storage", "prepare-lvm-node.sh"),
])
def test_host_scripts_ship_with_their_role_and_are_never_run_by_a_maintainer_script(debs, tmp_path, package, script, source):
    mode, owner, target = entries(debs[package])[f"/usr/sbin/{script}"]
    assert (mode, owner, target) == ("-rwxr-xr-x", "root/root", None)
    dest = tmp_path / "x"
    subprocess.run(["dpkg-deb", "-x", str(debs[package]), str(dest)], check=True)
    assert (dest / "usr/sbin" / script).read_bytes() == (ROOT / "scripts" / source).read_bytes()
    for pkg, deb in debs.items():
        ctrl = tmp_path / f"ctrl-{pkg}"
        subprocess.run(["dpkg-deb", "--control", str(deb), str(ctrl)], check=True)
        for member in ctrl.iterdir():
            if member.name != "control":
                assert script not in member.read_text(), (pkg, member.name)
                assert source not in member.read_text(), (pkg, member.name)


def test_compute_ships_only_the_two_host_scripts(debs):
    assert shipped_files(debs["cloudx-compute"]) == ["/usr/sbin/cloudx-setup-ceph-secret", "/usr/sbin/cloudx-setup-compute"]


def test_storage_ships_only_the_storage_host_script(debs):
    assert shipped_files(debs["cloudx-storage"]) == ["/usr/sbin/cloudx-setup-storage"]
