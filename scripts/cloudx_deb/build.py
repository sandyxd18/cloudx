"""Build the CloudX .deb packages (cloudx-common, cloudx-db, one per api/daemon unit, role meta packages and
cloudx-cli) from the manifest."""
import re
import shutil
import subprocess
from pathlib import Path

from .manifest import Unit, load_manifest
from .render import render_api_unit, render_daemon_unit, render_nginx, render_wrapper

ROOT = Path(__file__).resolve().parents[2]
REVISION = "1"
MAINTAINER = "CloudX <root@localhost>"
VENV = "/opt/cloudx/venv"
CLI_VENV = "/opt/cloudx/cli/venv"
SERVICES = "opt/cloudx/services"
UNIT_DIR = "lib/systemd/system"
TMPFILES = "/usr/lib/tmpfiles.d/cloudx.conf"
CONF_SAMPLE = "/usr/share/cloudx/cloudx.conf.sample"
EXCLUDE_ANYWHERE = {"__pycache__", "tests", "node_modules", ".pytest_cache"}
EXCLUDE_TOP = {"migrations", "frontend"}
META_RECOMMENDS = {
    "controller": ["postgresql", "rabbitmq-server", "memcached", "ovn-central"],
    "compute": ["libvirt-daemon-system", "qemu-kvm", "openvswitch-switch", "ovn-host", "qemu-utils"],
    "storage": [],
}
# Host preparation the packages cannot do on their own (they need addresses or disks), shipped with the role's meta package.
HOST_SCRIPTS = {
    "compute": {"usr/sbin/cloudx-setup-compute": "setup-compute-node.sh",
                "usr/sbin/cloudx-setup-ceph-secret": "setup-ceph-libvirt-secret.sh"},
    "storage": {"usr/sbin/cloudx-setup-storage": "prepare-lvm-node.sh"},
}
_DEFAULT_SECRET = re.compile(r"^auth_secret_key *= *cloudx-secret-key-change-in-production.*(\n|$)", re.MULTILINE)


def control_file(pkg: str, version: str, depends: list[str], description: str,
                 recommends: list[str] | None = None) -> str:
    rec = f"Recommends: {', '.join(recommends)}\n" if recommends else ""
    return (f"Package: {pkg}\nVersion: {version}\nArchitecture: amd64\nMaintainer: {MAINTAINER}\n"
            f"Section: admin\nPriority: optional\nDepends: {', '.join(depends)}\n{rec}Description: {description}\n")


def strip_default_secret(text: str) -> str:
    """Old samples shipped a default auth_secret_key that would override cloudx.conf (install-systemd.sh)."""
    return _DEFAULT_SECRET.sub("", text)


def relocate(data: bytes, staging_venv: bytes, venv: str = VENV) -> bytes:
    """Point a venv text file at `venv` (default /opt/cloudx/venv). pip writes a /bin/sh trampoline instead of a
    shebang when the staging path is too long for one; that becomes a plain shebang again."""
    trampoline = re.compile(rb"\A#!/bin/sh\n'''exec' \"?(" + re.escape(venv).encode()
                            + rb"/bin/python[0-9.]*)\"? \"\$0\" \"\$@\"\n' '''\n")
    return trampoline.sub(rb"#!\1\n", data.replace(staging_venv, venv.encode()), count=1)


def _write(path: Path, text: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(mode)


def _script(body: str) -> str:
    return f"#!/bin/sh\nset -e\n\n{body}\nexit 0\n"


def _copy_code(src: Path, dst: Path) -> None:
    def ignore(directory, names):
        skip = {n for n in names if n in EXCLUDE_ANYWHERE or n.endswith((".pyc", ".pyo", ".egg-info"))}
        if Path(directory) == src:
            skip |= EXCLUDE_TOP & set(names)
        return skip
    shutil.copytree(src, dst, ignore=ignore, symlinks=True)


def _systemd_scripts(units: list[str], start: str) -> dict[str, str]:
    """What dh_installsystemd generates: enable and (re)start `start`; stop every shipped unit on remove."""
    all_units = " ".join(f"'{u}'" for u in units)
    postinst = f"""if [ "$1" = "configure" ] || [ "$1" = "abort-upgrade" ] || [ "$1" = "abort-deconfigure" ] || [ "$1" = "abort-remove" ]; then
    deb-systemd-helper unmask '{start}' >/dev/null || true
    if deb-systemd-helper --quiet was-enabled '{start}'; then
        deb-systemd-helper enable '{start}' >/dev/null || true
    else
        deb-systemd-helper update-state '{start}' >/dev/null || true
    fi
    if [ -d /run/systemd/system ]; then
        systemctl --system daemon-reload >/dev/null || true
        if [ -n "$2" ]; then
            _action=restart
        else
            _action=start
        fi
        deb-systemd-invoke $_action '{start}' >/dev/null || true
    fi
fi
"""
    prerm = f"""if [ -z "${{DPKG_ROOT:-}}" ] && [ "$1" = remove ] && [ -d /run/systemd/system ]; then
    deb-systemd-invoke stop {all_units} >/dev/null || true
fi
"""
    postrm = f"""if [ "$1" = remove ] && [ -d /run/systemd/system ]; then
    systemctl --system daemon-reload >/dev/null || true
fi
if [ "$1" = purge ] && [ -x /usr/bin/deb-systemd-helper ]; then
    deb-systemd-helper purge {all_units} >/dev/null || true
fi
"""
    return {"postinst": _script(postinst), "prerm": _script(prerm), "postrm": _script(postrm)}


def _nginx_postinst() -> str:
    """Appended to an nginx-fronted package's postinst. A missing, stopped or broken nginx never fails the install."""
    return """
if [ "$1" = "configure" ] && [ -d /run/systemd/system ] && command -v nginx >/dev/null 2>&1; then
    if nginx -t; then
        deb-systemd-invoke reload nginx.service || echo "warning: nginx did not reload; run: systemctl reload nginx" >&2
    else
        echo "warning: nginx -t failed, so nginx was not reloaded; fix the config shown above and reload nginx" >&2
    fi
fi
"""


def _nginx_postrm() -> str:
    """Appended to an nginx-fronted package's postrm. dpkg deletes the snippet on purge; nginx must stop serving it."""
    return """
if [ "$1" = purge ] && [ -d /run/systemd/system ] && command -v nginx >/dev/null 2>&1; then
    if nginx -t >/dev/null 2>&1; then
        deb-systemd-invoke reload nginx.service >/dev/null || echo "warning: nginx did not reload; run: systemctl reload nginx" >&2
    else
        echo "warning: nginx -t failed, so nginx was not reloaded; fix its config and run: systemctl reload nginx" >&2
    fi
fi
"""


def _common_postinst() -> str:
    var_lib = " ".join(f"/var/lib/cloudx/{d}" for d in ("images", "object-store", "compute", "ha-watchdog", "fs-state"))
    return _script(f"""if [ "$1" = "configure" ]; then
    if ! getent group cloudx >/dev/null; then
        groupadd --system cloudx
    fi
    if ! getent passwd cloudx >/dev/null; then
        useradd --system --gid cloudx --home-dir /opt/cloudx --no-create-home --shell /usr/sbin/nologin \\
            --comment "CloudX Service User" cloudx
    fi
    for d in /var/lib/cloudx {var_lib}; do
        mkdir -p "$d"
        chown cloudx:cloudx "$d"
    done
    # BIND runs from here, rendered by cloudx-dns-worker (root).
    mkdir -p /var/lib/cloudx/dns
    systemd-tmpfiles --create {TMPFILES} || true
    # Creates cloudx.conf on first install and fills secrets that are missing; existing secrets are never changed.
    {VENV}/bin/cloudx-manage conf init --path /etc/cloudx/cloudx.conf --sample {CONF_SAMPLE}
    chown root:cloudx /etc/cloudx/cloudx.conf
    chmod 640 /etc/cloudx/cloudx.conf
    # Per-unit confs ship 0644 and may hold DB passwords; the directory keeps them private.
    chown root:cloudx /etc/cloudx
    chmod 750 /etc/cloudx
fi
""")


def _new_venv(staging: Path, target: str, requirements: Path, constraints: Path) -> tuple[Path, list[str]]:
    venv = staging / target.lstrip("/")
    # The system interpreter, so venv/bin/python points at /usr/bin/python3 on the target too.
    subprocess.run(["/usr/bin/python3", "-m", "venv", str(venv)], check=True)
    pip = [str(venv / "bin/python"), "-m", "pip", "install", "--quiet", "--no-cache-dir"]
    # The constraints pin every package, transitive ones included, so a rebuild ships the versions that were tested.
    subprocess.run([*pip, "-c", str(constraints), "-r", str(requirements)], check=True)
    return venv, pip


def _relocate_venv(venv: Path, target: str) -> None:
    prefix = str(venv).encode()
    for f in [*(venv / "bin").iterdir(), venv / "pyvenv.cfg"]:
        if f.is_symlink() or not f.is_file():
            continue
        data = f.read_bytes()
        if prefix in data and b"\0" not in data:
            f.write_bytes(relocate(data, prefix, target))


def _build_venv(staging: Path, work_dir: Path) -> None:
    venv, pip = _new_venv(staging, VENV, ROOT / "requirements.txt", ROOT / "packaging/constraints.txt")
    # Install the shared library from a copy so pip leaves no build/ or egg-info in the repo.
    src = work_dir / "shared-src"
    if src.exists():
        shutil.rmtree(src)
    shutil.copytree(ROOT / "services/shared", src / "shared",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "tests"))
    shutil.copy2(ROOT / "services/pyproject.toml", src / "pyproject.toml")
    subprocess.run([*pip, "--no-deps", str(src)], check=True)
    _relocate_venv(venv, VENV)


def _build_cli_venv(staging: Path, work_dir: Path) -> None:
    venv, pip = _new_venv(staging, CLI_VENV, ROOT / "cli/requirements.txt", ROOT / "packaging/cli-constraints.txt")
    src = work_dir / "cli-src"
    if src.exists():
        shutil.rmtree(src)
    shutil.copytree(ROOT / "cli", src, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "build", "*.egg-info"))
    subprocess.run([*pip, "--no-deps", str(src)], check=True)
    _relocate_venv(venv, CLI_VENV)


def _stage_common(staging: Path, version: str, work_dir: Path, with_venv: bool) -> None:
    if with_venv:
        _build_venv(staging, work_dir)
    (staging / "etc/cloudx").mkdir(parents=True)
    _write(staging / CONF_SAMPLE.lstrip("/"), (ROOT / "etc/cloudx/cloudx.conf.sample").read_text())
    _write(staging / "usr/share/cloudx/manifest.toml", (ROOT / "packaging/manifest.toml").read_text())
    _write(staging / TMPFILES.lstrip("/"), "d /run/cloudx 0755 cloudx cloudx -\n")
    (staging / "usr/bin").mkdir(parents=True)
    for cmd in ("cloudx-manage", "cloudx-db-migrate"):
        (staging / "usr/bin" / cmd).symlink_to(f"{VENV}/bin/{cmd}")
    _write(staging / "DEBIAN/control", control_file(
        "cloudx-common", version, ["python3 (>= 3.12)", "python3 (<< 3.13)"],
        "CloudX common files: Python environment, cloudx user, cloudx.conf and cloudx-manage"))
    _write(staging / "DEBIAN/postinst", _common_postinst(), 0o755)


def _stage_db(staging: Path, version: str) -> None:
    services = ROOT / "services"
    _copy_code(services / "migrations", staging / SERVICES / "migrations")
    for mig in sorted(services.glob("*/migrations")):
        _copy_code(mig, staging / SERVICES / mig.parent.name / "migrations")
    _write(staging / "DEBIAN/control", control_file(
        "cloudx-db", version, [f"cloudx-common (= {version})"],
        "CloudX database migrations (apply with cloudx-manage db sync)"))


def _stage_cli(staging: Path, version: str, work_dir: Path, with_venv: bool) -> None:
    if with_venv:
        _build_cli_venv(staging, work_dir)
    (staging / "usr/bin").mkdir(parents=True)
    (staging / "usr/bin/cloudx").symlink_to(f"{CLI_VENV}/bin/cloudx")
    _write(staging / "DEBIAN/control", control_file(
        "cloudx-cli", version, ["python3 (>= 3.12)", "python3 (<< 3.13)"],
        "CloudX command line client (cloudx), installable on its own"))


def _stage_meta(staging: Path, version: str, role: str, packaged: list[Unit]) -> None:
    members = [u for u in packaged if role in u.roles]
    # A unit with extra apt dependencies (dns-worker -> bind9) is only recommended, so the meta package never
    # pulls a distro service in by itself.
    depends = [f"{u.package} (= {version})" for u in members if not u.depends]
    recommends = [f"{u.package} (= {version})" for u in members if u.depends]
    if role == "controller":
        depends.append(f"cloudx-db (= {version})")
    recommends += ["cloudx-cli", *META_RECOMMENDS[role]]
    _write(staging / "DEBIAN/control", control_file(
        f"cloudx-{role}", version, depends, f"CloudX {role} node: every {role} service", recommends))
    for dest, source in HOST_SCRIPTS.get(role, {}).items():
        (staging / dest).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / "scripts" / source, staging / dest)  # bytes: the scripts hold non-ASCII text
        (staging / dest).chmod(0o755)


def _stage_unit(staging: Path, version: str, u: Unit, bundled: list[Unit]) -> None:
    _copy_code(ROOT / "services" / u.dir, staging / SERVICES / u.dir)
    _write(staging / "usr/bin" / u.package, render_wrapper(u), 0o755)
    source = (ROOT / "etc/systemd" / u.unit_file).read_text()
    unit = render_api_unit(u, source) if u.kind == "api" else render_daemon_unit(u, source)
    _write(staging / UNIT_DIR / u.unit_file, unit)
    for b in bundled:  # shipped verbatim, never enabled or started by the package
        _write(staging / UNIT_DIR / b.unit_file, (ROOT / "etc/systemd" / b.unit_file).read_text())
    sample = ROOT / "etc/cloudx" / u.name / f"{u.name}.conf.sample"
    conf = f"/etc/cloudx/{u.name}/{u.name}.conf"
    _write(staging / conf.lstrip("/"), strip_default_secret(sample.read_text()) if sample.exists() else "[DEFAULT]\n")
    conffiles = [conf]
    depends = [f"cloudx-common (= {version})", "init-system-helpers", *u.depends]
    nginx = u.kind == "api" and u.frontend == "nginx"
    if nginx:
        snippet = f"/etc/nginx/conf.d/cloudx-{u.name}.conf"
        _write(staging / snippet.lstrip("/"), render_nginx(u))
        conffiles.append(snippet)
        depends.append("nginx")
    _write(staging / "DEBIAN/conffiles", "".join(f"{c}\n" for c in conffiles))
    depends += [d for b in bundled for d in b.depends if d not in depends]
    port = f", port {u.port}" if u.port else ""
    _write(staging / "DEBIAN/control", control_file(u.package, version, depends, f"CloudX {u.name} ({u.kind}{port})"))
    scripts = _systemd_scripts([u.unit_file, *(b.unit_file for b in bundled)], u.unit_file)
    if nginx:
        scripts["postinst"] = scripts["postinst"].removesuffix("exit 0\n") + _nginx_postinst().lstrip("\n") + "exit 0\n"
        scripts["postrm"] = scripts["postrm"].removesuffix("exit 0\n") + _nginx_postrm().lstrip("\n") + "exit 0\n"
    for name, text in scripts.items():
        _write(staging / "DEBIAN" / name, text, 0o755)


def _normalize_modes(staging: Path) -> None:
    for path in [staging, *staging.rglob("*")]:
        if path.is_symlink():
            continue
        if path.is_dir():
            path.chmod(0o755)
        else:
            path.chmod(0o755 if path.stat().st_mode & 0o111 else 0o644)


def _installed_size(staging: Path) -> int:
    total = sum(p.lstat().st_size for p in staging.rglob("*")
                if not p.is_dir() and "DEBIAN" not in p.relative_to(staging).parts[:1])
    return max(1, -(-total // 1024))


def _dpkg_deb(staging: Path, out_dir: Path) -> Path:
    control = staging / "DEBIAN/control"
    fields = dict(line.split(": ", 1) for line in control.read_text().splitlines())
    control.write_text(control.read_text() + f"Installed-Size: {_installed_size(staging)}\n")
    _normalize_modes(staging)
    deb = out_dir / f"{fields['Package']}_{fields['Version']}_{fields['Architecture']}.deb"
    subprocess.run(["fakeroot", "dpkg-deb", "--build", "--root-owner-group", str(staging), str(deb)],
                   check=True, stdout=subprocess.DEVNULL)
    return deb


def build_all(out_dir: Path, version: str, *, with_venv: bool = True, only: list[str] | None = None,
              work_dir: Path | None = None) -> list[Path]:
    """Build the packages into out_dir; `version` is the VERSION file, the Debian revision is appended.

    `only` names packages without the cloudx- prefix ("common", "db", "image-api", "compute", "cli", ...).
    """
    out_dir, work_dir = Path(out_dir), Path(work_dir or ROOT / "build" / "deb")
    deb_version = f"{version}-{REVISION}"
    units = load_manifest()
    packaged = [u for u in units if u.kind != "unit"]
    names = ["common", "db", *(u.name for u in packaged), *META_RECOMMENDS, "cli"]
    unknown = sorted(set(only or ()) - set(names))
    if unknown:
        raise ValueError(f"no such package: {', '.join(unknown)}")
    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    built = []
    for name in names:
        if only is not None and name not in only:
            continue
        staging = work_dir / f"cloudx-{name}"
        if staging.exists():
            shutil.rmtree(staging)
        (staging / "DEBIAN").mkdir(parents=True)
        if name == "common":
            _stage_common(staging, deb_version, work_dir, with_venv)
        elif name == "db":
            _stage_db(staging, deb_version)
        elif name == "cli":
            _stage_cli(staging, deb_version, work_dir, with_venv)
        elif name in META_RECOMMENDS:
            _stage_meta(staging, deb_version, name, packaged)
        else:
            u = next(u for u in packaged if u.name == name)
            _stage_unit(staging, deb_version, u, [b for b in units if b.bundle_with == name])
        built.append(_dpkg_deb(staging, out_dir))
    return built
