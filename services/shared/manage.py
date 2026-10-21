"""cloudx-manage: administration commands for an installed CloudX node.

    cloudx-manage db sync [service ...]                  apply database migrations (all services by default)
    cloudx-manage conf init [--path P] [--sample S]      create cloudx.conf from the sample and fill in missing secrets

`conf init` never changes a secret that already has a value: rotating auth_secret_key would invalidate every token.
"""
import argparse
import os
import re
import secrets
import string
import sys
import tempfile
from pathlib import Path

from . import migrate

CONF_PATH = "/etc/cloudx/cloudx.conf"
SAMPLE_PATH = "/usr/share/cloudx/cloudx.conf.sample"
CONF_MODE = 0o640
_ALNUM = string.ascii_letters + string.digits

SECRETS = {  # same keys and strength as scripts/install-systemd.sh
    "auth_secret_key": lambda: secrets.token_hex(32),
    "auth_admin_password": lambda: "".join(secrets.choice(_ALNUM) for _ in range(18)),
    "key_manager_master_key": lambda: secrets.token_hex(32),
    "metrics_token": lambda: secrets.token_hex(24),
    "internal_token": lambda: secrets.token_hex(24),
}


def _has_value(lines: list[str], key: str) -> bool:
    return any(re.match(rf"{key}[ \t]*=[ \t]*\S", line) for line in lines)


def init_conf(path: Path, sample: Path) -> list[str]:
    """Create `path` from `sample` if missing, then fill each absent or empty secret. Returns the keys generated."""
    path = Path(path)
    existing = path.stat() if path.exists() else None
    lines = (path if existing else Path(sample)).read_text().splitlines(keepends=True)

    missing = [key for key in SECRETS if not _has_value(lines, key)]
    if not missing and existing:
        return []

    lines = [line for line in lines if not any(re.match(rf"{key}[ \t]*=", line) for key in missing)]
    new = [f"{key} = {SECRETS[key]()}\n" for key in missing]
    at = next((i + 1 for i, line in enumerate(lines) if line.strip() == "[DEFAULT]"), None)
    if at is None:
        lines[:0] = ["[DEFAULT]\n"]
        at = 1
    if not lines[at - 1].endswith("\n"):
        lines[at - 1] += "\n"
    lines[at:at] = new

    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        os.fchmod(fd, CONF_MODE)  # before any secret is written
        with os.fdopen(fd, "w") as f:
            f.write("".join(lines))
            f.flush()
            os.fsync(f.fileno())
        if existing:
            try:
                os.chown(tmp, existing.st_uid, existing.st_gid)
            except PermissionError:
                pass
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return missing


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="cloudx-manage", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    groups = ap.add_subparsers(dest="group", required=True)

    db = groups.add_parser("db", help="database commands").add_subparsers(dest="action", required=True)
    sync = db.add_parser("sync", help="apply migrations to head")
    sync.add_argument("services", nargs="*", help="services to migrate (default: all)")

    conf = groups.add_parser("conf", help="configuration commands").add_subparsers(dest="action", required=True)
    init = conf.add_parser("init", help="create cloudx.conf and generate missing secrets")
    init.add_argument("--path", default=CONF_PATH)
    init.add_argument("--sample", default=SAMPLE_PATH)

    args = ap.parse_args(argv)
    if (args.group, args.action) == ("db", "sync"):
        migrate.main(args.services)  # exits non-zero by itself when a migration fails
        return 0
    if (args.group, args.action) == ("conf", "init"):
        if not Path(args.path).exists() and not Path(args.sample).exists():
            sys.exit(f"cannot create {args.path}: sample {args.sample} not found")
        generated = init_conf(Path(args.path), Path(args.sample))
        print(f"{args.path}: generated {', '.join(generated)}" if generated else f"{args.path}: nothing to generate")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
