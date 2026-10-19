"""Input checks for database instances (cloud-init and container env only ever see validated values)."""
import re
import secrets

from shared.fs_validate import check_access, check_size  # noqa: F401  (check_size re-exported)

ENGINES = ("postgresql", "mariadb")
_MATRIX = {"vm": ("local", "block", "file"), "container": ("local",)}
_PW = re.compile(r"\A[A-Za-z0-9_-]{12,64}\Z")

def check_engine(s: str) -> str:
    if s not in ENGINES:
        raise ValueError(f"engine must be one of {', '.join(ENGINES)}")
    return s

def check_backend_storage(backend: str, storage: str) -> None:
    if storage not in _MATRIX.get(backend, ()):
        raise ValueError(f"storage '{storage}' is not supported on backend '{backend}' "
                         "(vm: local|block|file, container: local)")

def check_password(s: str) -> str:
    if not isinstance(s, str) or not _PW.match(s):
        raise ValueError("password must be 12-64 characters of A-Z a-z 0-9 _ -")
    return s

def gen_password() -> str:
    return secrets.token_urlsafe(18)  # 24 chars of the allowed alphabet

def check_cidr(s: str) -> str:
    return check_access(s, "rw")[0]