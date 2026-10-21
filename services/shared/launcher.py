"""Entry points behind /usr/bin/cloudx-<unit>: run a CloudX API under gunicorn or a daemon in-process."""
import os
import runpy
import sys
from pathlib import Path
from typing import List, Mapping, NoReturn

from shared.config import load_service_config


def resolve_workers(unit: str, default: int, config: Mapping[str, str]) -> int:
    if "workers" not in config:
        return default
    raw = config["workers"]
    try:
        workers = int(raw)
    except (TypeError, ValueError):
        workers = 0
    if workers < 1:
        raise ValueError(f"{unit}: workers must be a positive integer, got '{raw}'")
    return workers


def gunicorn_argv(app_dir: str, bind: str, workers: int, frontend: str) -> List[str]:
    """Arguments for gunicorn (without the executable).

    --forwarded-allow-ips is never '*': uvicorn would take the first X-Forwarded-For entry, which the client
    supplies. Behind nginx it is empty so the header is ignored; direct services keep gunicorn's default.
    The control socket is off: gunicorn 26 would create it under the service user's home, which is not writable.
    """
    argv = [
        "-k", "uvicorn.workers.UvicornWorker",
        "-w", str(workers),
        "--bind", bind,
        "--chdir", app_dir,
        "--no-control-socket",
    ]
    if frontend == "nginx":
        argv.append("--forwarded-allow-ips=")
    argv.append("app.main:app")
    return argv


def run_api(name: str, app_dir: str, port: int, workers: int, frontend: str) -> NoReturn:
    config = load_service_config(name)
    try:
        workers = resolve_workers(name, workers, config)
    except ValueError as e:
        print(e, file=sys.stderr)
        sys.exit(2)
    bind = f"unix:/run/cloudx/{name}.sock" if frontend == "nginx" else f"0.0.0.0:{port}"
    gunicorn = str(Path(sys.executable).parent / "gunicorn")
    os.execv(gunicorn, [gunicorn, *gunicorn_argv(app_dir, bind, workers, frontend)])


def run_daemon(name: str, app_dir: str) -> NoReturn:
    load_service_config(name)
    os.chdir(app_dir)
    sys.path.insert(0, app_dir)
    runpy.run_module("app.main", run_name="__main__")
