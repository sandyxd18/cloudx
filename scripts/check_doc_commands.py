#!/usr/bin/env python3
"""Check that the `cloudx ...` commands shown in the docs exist in the CLI.

Reads fenced code blocks, takes every line that starts with `cloudx `, walks the real click command tree and reports
unknown subcommands and unknown --options. Placeholders and values are not checked, only command names and option names.

    python scripts/check_doc_commands.py            # README.md, SETUP.md and docs/*.md (not release notes or roadmaps)
    python scripts/check_doc_commands.py docs/ha.md
"""
import re
import shlex
import sys
from pathlib import Path

import click

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cli"))
from cloudx_cli.main import cli  # noqa: E402

# Pages that describe history or a plan, not the current CLI.
SKIP = {"roadmap-openstack-parity.md", "feature-comparison.md", "tarball-install.md"}


def default_files() -> list[Path]:
    return [ROOT / "README.md", ROOT / "SETUP.md", *sorted(p for p in (ROOT / "docs").glob("*.md") if p.name not in SKIP)]


def commands_in(path: Path):
    for block in re.findall(r"```(?:bash|sh|console)?\n(.*?)```", path.read_text(), re.S):
        for line in re.sub(r"\\\n\s*", " ", block).splitlines():
            line = line.split(" #")[0].strip()
            if line.startswith("cloudx "):
                yield line


def problem(line: str) -> str | None:
    try:
        tokens = shlex.split(line)[1:]
    except ValueError:
        return None
    cmd, name, i = cli, "cloudx", 0
    while i < len(tokens) and isinstance(cmd, click.Group):
        token = tokens[i]
        if token.startswith("-"):  # a global option such as -o json
            opt = next((p for p in cmd.params if token in p.opts), None)
            i += 2 if opt is not None and not opt.is_flag else 1
            continue
        sub = cmd.commands.get(token)
        if sub is None:
            return f"no subcommand '{token}' under '{name}' (has: {', '.join(sorted(cmd.commands))})"
        cmd, name, i = sub, f"{name} {token}", i + 1
    if isinstance(cmd, click.Group):
        return None
    known = {o for p in cmd.params for o in p.opts + p.secondary_opts} | {"-h", "--help"}
    unknown = [t.split("=")[0] for t in tokens[i:] if t.startswith("--") and t.split("=")[0] not in known]
    if unknown:
        return f"'{name}' has no option {unknown} (has: {sorted(o for o in known if o.startswith('--'))})"
    return None


def problems(paths: list[Path]) -> list[str]:
    found = []
    for path in paths:
        for line in commands_in(path):
            if (reason := problem(line)):
                found.append(f"{path.relative_to(ROOT)}: {line[:120]}\n    {reason}")
    return found


if __name__ == "__main__":
    files = [Path(a).resolve() for a in sys.argv[1:]] or default_files()
    bad = problems(files)
    print("\n".join(bad) if bad else f"ok: commands in {len(files)} files exist")
    sys.exit(1 if bad else 0)
