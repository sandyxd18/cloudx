import os
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

DPKG_DEB = """#!/usr/bin/env python3
import sys

if sys.argv[1] == "--field":
    print({"Package": "cloudx-x"}.get(sys.argv[3], "set"))
else:  # --contents: the copyright entry comes first, then more output than a pipe holds (as for a package with a venv)
    out = sys.stdout
    out.write("-rw-r--r-- root/root 10 2026-10-26 00:00 ./usr/share/doc/cloudx-x/copyright\\n")
    for i in range(200000):
        out.write(f"-rw-r--r-- root/root 10 2026-10-26 00:00 ./opt/cloudx/venv/lib/file{i}.py\\n")
"""


def test_an_early_copyright_entry_is_not_reported_missing(tmp_path):
    # `dpkg-deb --contents | grep -q` made grep exit at the first match, dpkg-deb died of SIGPIPE, and pipefail turned
    # that into "no copyright file". Which entry comes first depends on the file system, so it only failed in CI.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("dpkg-deb", DPKG_DEB), ("lintian", "#!/bin/sh\nexit 0\n")):
        path = bin_dir / name
        path.write_text(body)
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    debs = tmp_path / "debs"
    debs.mkdir()
    (debs / "cloudx-x_1-1_amd64.deb").write_bytes(b"")
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    result = subprocess.run(["bash", str(ROOT / "scripts/check-debs.sh"), str(debs)], env=env, text=True,
                            capture_output=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert "1 packages passed" in result.stdout
