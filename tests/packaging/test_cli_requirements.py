import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def third_party_imports(package_dir: Path) -> set[str]:
    own = {p.stem for p in package_dir.glob("*.py")} | {package_dir.name}
    names = set()
    for path in package_dir.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                names |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names.add(node.module.split(".")[0])
    return {n for n in names if n not in sys.stdlib_module_names and n not in own}


def requirement_names(path: Path) -> set[str]:
    lines = (line.split("#")[0].strip() for line in path.read_text().splitlines())
    return {re.split(r"[<>=!~\[; ]", line, maxsplit=1)[0].lower().replace("_", "-") for line in lines if line}


def test_cli_requirements_cover_every_third_party_import():
    # cloudx-cli's venv is built from cli/requirements.txt alone; a missing entry installs but crashes on import.
    missing = {n for n in third_party_imports(ROOT / "cli/cloudx_cli")
               if n.lower().replace("_", "-") not in requirement_names(ROOT / "cli/requirements.txt")}
    assert not missing, f"cli/requirements.txt lacks: {sorted(missing)}"
