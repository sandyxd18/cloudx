"""The packaged venvs are installed with pip constraints, so two builds of one commit ship the same dependencies.

requirements.txt only gives ranges (fastapi>=0.115.0) and leaves the transitive packages free: a rebuild a day later
already pulled starlette 1.10.0 instead of 1.9.0, a version nobody had run."""
import re
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

from cloudx_deb import build

ROOT = Path(__file__).resolve().parents[2]
CONSTRAINTS = ROOT / "packaging" / "constraints.txt"
CLI_CONSTRAINTS = ROOT / "packaging" / "cli-constraints.txt"
PIN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*==[A-Za-z0-9.!+_\-]+$")


def _lines(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text().splitlines() if line.strip() and not line.lstrip().startswith("#")]


def _pins(path: Path) -> dict[str, Version]:
    return {canonicalize_name(line.split("==")[0]): Version(line.split("==")[1]) for line in _lines(path)}


class _Stop(Exception):
    pass


def test_new_venv_installs_with_the_constraints_file(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(build.subprocess, "run", lambda cmd, **kwargs: calls.append(list(cmd)))
    requirements, constraints = tmp_path / "requirements.txt", tmp_path / "constraints.txt"

    build._new_venv(tmp_path / "staging", "/opt/cloudx/venv", requirements, constraints)

    install = next(c for c in calls if "pip" in c and "-r" in c)
    assert install[install.index("-c") + 1] == str(constraints)
    assert install[install.index("-r") + 1] == str(requirements)


@pytest.mark.parametrize("builder, constraints_file", [
    ("_build_venv", "constraints.txt"),
    ("_build_cli_venv", "cli-constraints.txt"),
])
def test_each_venv_is_built_with_its_own_constraints(tmp_path, monkeypatch, builder, constraints_file):
    seen = []

    def fake_new_venv(*args):
        seen.append(args)
        raise _Stop

    monkeypatch.setattr(build, "_new_venv", fake_new_venv)
    with pytest.raises(_Stop):
        getattr(build, builder)(tmp_path / "staging", tmp_path / "work")

    assert seen[0][3] == ROOT / "packaging" / constraints_file


@pytest.mark.parametrize("path", [CONSTRAINTS, CLI_CONSTRAINTS], ids=lambda p: p.name)
def test_constraints_are_exact_pins_without_duplicates_or_our_own_packages(path):
    lines = _lines(path)
    assert lines, f"{path.name} is empty"
    assert [line for line in lines if not PIN.match(line)] == []
    names = [canonicalize_name(line.split("==")[0]) for line in lines]
    assert len(names) == len(set(names))
    assert not {"cloudx-shared", "cloudx-cli"} & set(names), "installed from source, not from an index"


@pytest.mark.parametrize("requirements, constraints", [
    (ROOT / "requirements.txt", CONSTRAINTS),
    (ROOT / "cli" / "requirements.txt", CLI_CONSTRAINTS),
], ids=["runtime", "cli"])
def test_every_requirement_has_a_pin_that_satisfies_it(requirements, constraints):
    pins = _pins(constraints)
    for line in _lines(requirements):
        requirement = Requirement(line)
        name = canonicalize_name(requirement.name)
        assert name in pins, f"{requirement.name} is in {requirements.name} but not pinned in {constraints.name}"
        assert pins[name] in requirement.specifier, f"{requirement.name}=={pins[name]} does not satisfy {line}"
