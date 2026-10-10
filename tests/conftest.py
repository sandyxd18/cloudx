import os

import pytest

# Services read these at import time and refuse to start without them (see scripts/install-systemd.sh).
os.environ.setdefault("AUTH_SECRET_KEY", "test-secret-key-not-for-production-32b")
os.environ.setdefault("AUTH_ADMIN_PASSWORD", "test-admin-password")
os.environ.setdefault("KEY_MANAGER_MASTER_KEY", "test-master-key-not-for-production-32b")
os.environ.setdefault("CLOUDX_ALLOW_DEFAULT_DB", "1")


def pytest_addoption(parser):
    parser.addoption("--live", action="store_true", help="also run tests/live (needs a running CloudX lab)")


def pytest_collection_modifyitems(config, items):
    """tests/live needs a lab: marked `live` and skipped unless --live is given."""
    skip = pytest.mark.skip(reason="needs a running CloudX lab; run with --live")
    for item in items:
        if item.path.parent.name == "live":
            item.add_marker(pytest.mark.live)
            if not config.getoption("--live"):
                item.add_marker(skip)


@pytest.fixture(autouse=True)
def _isolate_environ():
    """Services and load_service_config() write to os.environ; no test may leak that into the next one."""
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)


@pytest.fixture
def networking(monkeypatch, tmp_path):
    """networking-api freshly imported on a sqlite DB (see tests/stack.py)."""
    from tests.stack import fresh_service

    return fresh_service("networking", monkeypatch, tmp_path)


@pytest.fixture
def cloudx():
    """Run the cloudx CLI as a subprocess: cloudx("server", "--help") -> CompletedProcess."""
    import subprocess
    import sys
    from pathlib import Path

    cli_dir = Path(__file__).resolve().parents[1] / "cli"
    return lambda *args: subprocess.run(
        [sys.executable, "-m", "cloudx_cli.main", *args], cwd=cli_dir, capture_output=True, text=True
    )
