"""Input checks for container create requests (nothing here ever reaches a shell)."""
import re

_IMAGE = re.compile(r"\A[a-z0-9][a-z0-9._/-]{0,127}(:[A-Za-z0-9._-]{1,128})?\Z")
_ENV = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]{0,63}\Z")


def check_image(s: str) -> str:
    if not isinstance(s, str) or not _IMAGE.match(s):
        raise ValueError("invalid image reference (lowercase repo, optional :tag)")
    return s


def check_env(env: dict) -> dict[str, str]:
    if len(env) > 50:
        raise ValueError("at most 50 environment variables")
    for k, v in env.items():
        if not _ENV.match(str(k)):
            raise ValueError(f"invalid environment variable name: {k!r}")
        if not isinstance(v, str) or "\n" in v or "\r" in v or "\0" in v or len(v) > 4096:
            raise ValueError(f"invalid value for {k}")
    return dict(env)


def check_limits(cpu: float, memory_mb: int) -> tuple[float, int]:
    if not 0.1 <= cpu <= 4:
        raise ValueError("cpu must be between 0.1 and 4")
    if not 64 <= memory_mb <= 4096:
        raise ValueError("memory_mb must be between 64 and 4096")
    return cpu, memory_mb
