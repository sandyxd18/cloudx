"""The one way CloudX services run external commands (lvm, qemu-img, rbd, ovn-nbctl, virsh, ...).

    from shared.proc import run, output

    run(["lvcreate", "-L", "5G", "-n", name, vg])               # raises CommandError on failure
    rc = run(["lvdisplay", dev], check=False).returncode        # never raises for a failing command
    out = output(["lvs", "--noheadings", vg])                   # stripped stdout
    run(["qemu-img", "convert", src, dst], timeout=None)        # long copy: unlimited, and says so

Why a wrapper instead of `subprocess.run` at every call site:
  * arguments are always a list (a string raises TypeError), so nothing is ever interpreted by a shell;
  * every command has a timeout (default 60 s); `timeout=None` is allowed but must be written explicitly;
  * a missing binary or a timeout is reported the same way as a failing command (CommandError, or return code
    127 / 124 with `check=False`) instead of an unrelated exception type;
  * `secrets=[...]` are masked in logs and error messages (CommandError ends up in API error fields).
"""
import logging
import subprocess
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

log = logging.getLogger("cloudx.proc")

DEFAULT_TIMEOUT = 60
NOT_FOUND = 127  # same convention as a shell
TIMED_OUT = 124  # same as coreutils `timeout`
_MAX_DETAIL = 2000


class CommandError(RuntimeError):
    """A command exited non-zero, timed out, or does not exist. `cmd` has secrets masked."""

    def __init__(self, cmd: list[str], returncode: int, stdout: str = "", stderr: str = "", timeout: float | None = None):
        self.cmd, self.returncode, self.stdout, self.stderr, self.timeout = cmd, returncode, stdout, stderr, timeout
        name = Path(cmd[0]).name if cmd else "command"
        if returncode == TIMED_OUT and timeout is not None:
            msg = f"{name} timed out after {timeout:g}s"
        elif returncode == NOT_FOUND:
            msg = f"{name}: command not found"
        else:
            detail = (stderr or stdout or "").strip()[:_MAX_DETAIL]
            msg = f"{name} failed (exit {returncode})" + (f": {detail}" if detail else "")
        super().__init__(msg)


def _mask(value: str, secrets: Sequence[str]) -> str:
    for s in secrets:
        if s:
            value = value.replace(s, "***")
    return value


def run(
    cmd: Sequence[Any],
    *,
    check: bool = True,
    timeout: float | None = DEFAULT_TIMEOUT,
    input: str | bytes | None = None,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    text: bool = True,
    secrets: Sequence[str] = (),
) -> subprocess.CompletedProcess:
    """Run `cmd` (a list) and capture its output. See the module docstring."""
    if isinstance(cmd, (str, bytes)) or not cmd:
        raise TypeError("cmd must be a non-empty list of arguments, not a string")
    argv = [str(a) for a in cmd]
    shown = [_mask(a, secrets) for a in argv]
    started = time.monotonic()
    try:
        proc = subprocess.run(argv, capture_output=True, text=text, input=input, cwd=cwd, env=dict(env) if env else None,
                              timeout=timeout)
    except FileNotFoundError:
        proc = subprocess.CompletedProcess(argv, NOT_FOUND, "" if text else b"", f"{argv[0]}: command not found" if text else b"")
    except subprocess.TimeoutExpired:
        proc = subprocess.CompletedProcess(argv, TIMED_OUT, "" if text else b"", f"timed out after {timeout:g}s" if text else b"")
    stdout, stderr = proc.stdout, proc.stderr
    if text:
        stdout, stderr = _mask(stdout or "", secrets), _mask(stderr or "", secrets)
    log.debug("exec %s -> %s (%.2fs)", " ".join(shown)[:300], proc.returncode, time.monotonic() - started)
    result = subprocess.CompletedProcess(shown, proc.returncode, stdout, stderr)
    if check and proc.returncode != 0:
        raise CommandError(shown, proc.returncode, stdout if text else "", stderr if text else "", timeout)
    return result


def output(cmd: Sequence[Any], **kwargs) -> str:
    """Run a command that must succeed (unless check=False) and return its stripped stdout."""
    return (run(cmd, **kwargs).stdout or "").strip()


def succeeds(cmd: Sequence[Any], **kwargs) -> bool:
    """For probes (`lvdisplay`, `vgdisplay`, `ip link show`, ...): True if exit 0, False if the command ran and said no.

    Raises CommandError when the probe itself could not run (binary missing, timed out). Callers must treat that as
    "unknown", never as "does not exist": a reconciler that reads it as missing would act on a failed check.
    """
    kwargs["check"] = False
    res = run(cmd, **kwargs)
    if res.returncode in (NOT_FOUND, TIMED_OUT):
        raise CommandError(res.args, res.returncode, res.stdout, res.stderr, kwargs.get("timeout", DEFAULT_TIMEOUT))
    return res.returncode == 0
