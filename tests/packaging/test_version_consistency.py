import configparser
import subprocess
import tomllib
from pathlib import Path

from packaging.version import Version

ROOT = Path(__file__).resolve().parents[2]


def versions() -> dict[str, Version]:
    cfg = configparser.ConfigParser()
    cfg.read(ROOT / "cli/setup.cfg")
    return {
        "VERSION": Version((ROOT / "VERSION").read_text().strip()),
        "services/pyproject.toml": Version(tomllib.loads((ROOT / "services/pyproject.toml").read_text())["project"]["version"]),
        "cli/setup.cfg": Version(cfg["metadata"]["version"]),
    }


def test_every_version_in_the_tree_is_the_same_release():
    # `1.0.0-rc1` (tag, VERSION) and `1.0.0rc1` (Python packages) are the same version in PEP 440
    found = versions()
    assert len(set(found.values())) == 1, found


def test_bump_version_sets_all_of_them_and_refuses_a_bad_version(tmp_path):
    work = tmp_path / "repo"
    (work / "scripts").mkdir(parents=True)
    (work / "services").mkdir()
    (work / "cli").mkdir()
    (work / "scripts/bump-version.sh").write_text((ROOT / "scripts/bump-version.sh").read_text())
    (work / "VERSION").write_text("0.8.6\n")
    (work / "services/pyproject.toml").write_text('[project]\nname = "x"\nversion = "0.8.6"\n')
    (work / "cli/setup.cfg").write_text("[metadata]\nname = x\nversion = 0.8.6\n")
    run = lambda *a: subprocess.run(["bash", str(work / "scripts/bump-version.sh"), *a], text=True, capture_output=True)  # noqa: E731
    assert run("v1.0").returncode != 0 and run("").returncode != 0 and run("1.0.0 beta").returncode != 0
    assert (work / "VERSION").read_text() == "0.8.6\n"  # nothing changed by the refused calls
    done = run("1.0.0-rc1")
    assert done.returncode == 0, done.stderr
    assert (work / "VERSION").read_text() == "1.0.0-rc1\n"
    assert 'version = "1.0.0rc1"' in (work / "services/pyproject.toml").read_text()
    assert "version = 1.0.0rc1" in (work / "cli/setup.cfg").read_text()
