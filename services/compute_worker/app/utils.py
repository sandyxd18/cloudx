import shutil
from pathlib import Path

from shared.proc import CommandError, run  # noqa: F401  (re-exported: callers import them from here)


def tool_available(name: str) -> bool:
    return shutil.which(name) is not None


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def short_id(value: str, length: int = 8) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())[:length] or value[:length]
