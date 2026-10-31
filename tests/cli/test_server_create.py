import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "cli"))

from click.testing import CliRunner  # noqa: E402

from cloudx_cli.commands import compute  # noqa: E402
from cloudx_cli.main import cli  # noqa: E402


def _capture(monkeypatch):
    calls = []

    def fake_api(method, url, profile="default", **kw):
        calls.append((method, url, kw.get("json")))
        return {"id": "s1", "name": "vm"}

    monkeypatch.setattr(compute, "_api", fake_api)
    monkeypatch.setattr(compute, "DEFAULT_URLS", {"compute": "http://compute.test:8003"})
    return calls


def test_ipv6_flag_asks_for_an_ipv6_address(monkeypatch):
    calls = _capture(monkeypatch)
    res = CliRunner().invoke(cli, ["server", "create", "vm", "--image", "i", "--network", "n", "--ipv6"])
    assert res.exit_code == 0, res.output
    assert calls[0][2]["ipv6"] is True


def test_without_the_flag_the_request_does_not_mention_ipv6(monkeypatch):
    calls = _capture(monkeypatch)
    res = CliRunner().invoke(cli, ["server", "create", "vm", "--image", "i", "--network", "n"])
    assert res.exit_code == 0, res.output
    assert "ipv6" not in calls[0][2]
