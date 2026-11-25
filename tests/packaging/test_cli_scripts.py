"""cloudx-cli ships the operator scripts that run from an admin machine rather than on a node of one role (the
migration ssh setup reaches every compute node over ssh). Checked on the staging tree, so it needs no dpkg-deb."""
import stat
from pathlib import Path

from cloudx_deb import build

ROOT = Path(__file__).resolve().parents[2]


def test_the_cli_package_ships_the_migration_ssh_script(tmp_path):
    staging = tmp_path / "cloudx-cli"

    build._stage_cli(staging, "1.0.0-1", tmp_path / "work", with_venv=False)

    script = staging / "usr/bin/cloudx-setup-migration-ssh"
    assert script.read_bytes() == (ROOT / "scripts/setup-migration-ssh.sh").read_bytes()
    assert stat.S_IMODE(script.stat().st_mode) == 0o755
    assert build.HOST_SCRIPTS["cli"] == {"usr/bin/cloudx-setup-migration-ssh": "setup-migration-ssh.sh"}
