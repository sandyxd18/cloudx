import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "cli"))

from click.testing import CliRunner  # noqa: E402

from cloudx_cli.commands import compute  # noqa: E402
from cloudx_cli.main import cli  # noqa: E402


def _capture(monkeypatch):
    calls = []
    monkeypatch.setattr(compute, "_api", lambda method, url, profile="default", **kw: calls.append((method, url, kw.get("json"))) or {"server_id": "s1", "from": "n1", "to": "n2", "status": "MIGRATING"})
    monkeypatch.setattr(compute, "DEFAULT_URLS", {"compute": "http://compute.test:8003"})
    return calls


def test_live_migrate_posts_the_host_only_when_given(monkeypatch):
    calls = _capture(monkeypatch)

    res = CliRunner().invoke(cli, ["server", "live-migrate", "vm1"])
    assert res.exit_code == 0, res.output
    res = CliRunner().invoke(cli, ["server", "live-migrate", "vm1", "--host", "node-b"])
    assert res.exit_code == 0, res.output

    assert calls == [
        ("post", "http://compute.test:8003/servers/vm1/live-migrate", None),
        ("post", "http://compute.test:8003/servers/vm1/live-migrate", {"host": "node-b"}),
    ]
