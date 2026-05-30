import shutil
import subprocess
from pathlib import Path


class CommandError(RuntimeError):
    def __init__(self, cmd: list[str], stderr: str):
        super().__init__(f"{' '.join(cmd)} failed: {stderr}")
        self.cmd = cmd
        self.stderr = stderr


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, text=True, capture_output=True)
    if check and proc.returncode != 0:
        raise CommandError(cmd, proc.stderr.strip() or proc.stdout.strip())
    return proc


def tool_available(name: str) -> bool:
    return shutil.which(name) is not None


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def short_id(value: str, length: int = 8) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())[:length] or value[:length]
