"""Fresh import of one service's `app` package on a throwaway sqlite DB (tests only).

Services read DATABASE_URL and other env at import time and keep state (engine, RPC client, caches) in module
globals. Re-importing the whole package per test gives every test a clean slate, with no stale engine left in a
module that imported it by name.
"""
import importlib
import sys


class ServiceStack:
    """`stack.clients` returns the submodule; any other attribute is looked up across the package's submodules."""

    def __init__(self, svc: str):
        self._pkg = f"services.{svc}.app"

    def __getattr__(self, name):
        pkg = self._pkg
        if f"{pkg}.{name}" in sys.modules:
            return sys.modules[f"{pkg}.{name}"]
        for modname, mod in list(sys.modules.items()):
            if modname.startswith(pkg + ".") and mod is not None and hasattr(mod, name):
                return getattr(mod, name)
        raise AttributeError(name)


def fresh_service(svc: str, monkeypatch, tmp_path) -> ServiceStack:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / (svc + '.sqlite')}")
    pkg = f"services.{svc}.app"
    for name in [n for n in sys.modules if n.startswith(pkg + ".")]:
        del sys.modules[name]
        parent, _, child = name.rpartition(".")
        if parent in sys.modules and hasattr(sys.modules[parent], child):
            delattr(sys.modules[parent], child)   # `from . import x` would otherwise return the stale module
    importlib.import_module(f"{pkg}.main")
    stack = ServiceStack(svc)
    stack.models.Base.metadata.create_all(bind=stack.db.engine)
    return stack
