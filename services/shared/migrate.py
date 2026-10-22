"""cloudx-db-migrate: apply, inspect or create Alembic migrations for CloudX services.

    cloudx-db-migrate                      upgrade every service to head
    cloudx-db-migrate compute auth         upgrade only these services
    cloudx-db-migrate --current            show each service's current revision
    cloudx-db-migrate --revision compute -m "add server notes" --autogenerate

Each service's database URL is resolved the way the service resolves it (services/migrations/registry.py).
`--url` overrides it (single service only).
"""
import argparse
import importlib
import importlib.util
import os
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config

from .config import load_service_config
from .schema import version_table


def services_dir(explicit: str | None = None) -> Path:
    for candidate in (explicit, os.getenv("CLOUDX_SERVICES_DIR"), ".", "services", "/opt/cloudx/services"):  # "." first: run inside the tree, never a stale nested copy
        if candidate and (Path(candidate) / "migrations" / "registry.py").exists():
            return Path(candidate).resolve()
    sys.exit("cannot find the services directory (install cloudx-db, which ships it, or set --services-dir or CLOUDX_SERVICES_DIR)")


def load_registry(root: Path):
    spec = importlib.util.spec_from_file_location("cloudx_migration_registry", root / "migrations" / "registry.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def resolve_url(conf_name: str, env_vars: list[str], default_url: str) -> str:
    """Same lookup order as the service: /etc/cloudx conf (-> env), then the env vars, then the default."""
    saved = dict(os.environ)
    try:
        load_service_config(conf_name)
        for var in env_vars:
            if os.environ.get(var):
                return os.environ[var]
        return default_url
    finally:
        os.environ.clear()
        os.environ.update(saved)


def make_config(root: Path, service: str, url: str, models: str | None = None) -> Config:
    cfg = Config(str(root / "migrations" / "alembic.ini"), stdout=sys.stdout)
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.set_main_option("version_locations", str(root / service / "migrations" / "versions"))
    cfg.attributes["url"] = url
    cfg.attributes["version_table"] = version_table(service)
    if models:
        sys.path.insert(0, str(root.parent))
        cfg.attributes["target_metadata"] = importlib.import_module(models).Base.metadata
    return cfg


def upgrade(service: str, url: str, root: Path, revision: str = "head") -> None:
    command.upgrade(make_config(root, service, url), revision)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="cloudx-db-migrate", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("services", nargs="*", help="services to migrate (default: all)")
    ap.add_argument("--services-dir")
    ap.add_argument("--url", help="database URL (single service)")
    ap.add_argument("--current", action="store_true", help="show the current revision instead of upgrading")
    ap.add_argument("--revision", metavar="SERVICE", help="create a new revision for SERVICE")
    ap.add_argument("-m", "--message", default="schema change")
    ap.add_argument("--autogenerate", action="store_true", help="with --revision: diff models against the database")
    args = ap.parse_args(argv)

    root = services_dir(args.services_dir)
    reg = load_registry(root)
    registry = reg.SERVICES
    names = [args.revision] if args.revision else args.services or sorted(registry)
    unknown = [n for n in names if n not in registry]
    if unknown:
        sys.exit(f"unknown service(s): {', '.join(unknown)}. Known: {', '.join(sorted(registry))}")
    if args.url and len(names) != 1:
        sys.exit("--url needs exactly one service")

    sys.path.insert(0, str(root.parent))  # so `services.<svc>.app.models` imports for --autogenerate
    failed = []
    for name in names:
        conf_name, env_vars, default_db, models = registry[name]
        url = args.url or resolve_url(conf_name, env_vars, reg.DEFAULT_URL.format(db=default_db))
        shown = url.split("@")[-1] if "@" in url else url
        try:
            if args.revision:
                cfg = make_config(root, name, url, models)
                command.revision(cfg, message=args.message, autogenerate=args.autogenerate, rev_id=None)
            elif args.current:
                print(f"{name} ({shown}):", end=" ", flush=True)
                command.current(make_config(root, name, url))
            else:
                print(f"==> {name} ({shown})", flush=True)
                upgrade(name, url, root)
        except Exception as exc:  # keep going so one unreachable database does not hide the others
            failed.append(name)
            print(f"!! {name}: {type(exc).__name__}: {str(exc).splitlines()[0][:200]}", file=sys.stderr)
    if failed:
        sys.exit(f"migration failed for: {', '.join(failed)}")


if __name__ == "__main__":
    main()
