import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "cli"))

from click.testing import CliRunner  # noqa: E402

from cloudx_cli.commands import compute  # noqa: E402
from cloudx_cli.main import cli  # noqa: E402


def _capture(monkeypatch):
    calls = []
    monkeypatch.setattr(compute, "_api", lambda method, url, profile="default", **kw: calls.append((method, url, kw.get("json"))) or {"status": "ok"})
    monkeypatch.setattr(compute, "DEFAULT_URLS", {"compute": "http://compute.test:8003"})
    return calls


def test_resize_says_the_server_must_be_confirmed(monkeypatch):
    # the server is left in VERIFY_RESIZE; a user who is not told has no way to know the next step
    calls = _capture(monkeypatch)

    res = CliRunner().invoke(cli, ["server", "resize", "vm1", "--flavor", "big"])

    assert res.exit_code == 0, res.output
    assert calls == [("post", "http://compute.test:8003/servers/vm1/resize", {"flavor": "big"})]
    assert "confirm-resize" in res.output and "revert-resize" in res.output


def test_confirm_resize_calls_confirm(monkeypatch):
    calls = _capture(monkeypatch)

    res = CliRunner().invoke(cli, ["server", "confirm-resize", "vm1"])

    assert res.exit_code == 0, res.output
    assert calls == [("post", "http://compute.test:8003/servers/vm1/confirmResize", None)]


def test_revert_resize_calls_revert(monkeypatch):
    calls = _capture(monkeypatch)

    res = CliRunner().invoke(cli, ["server", "revert-resize", "vm1"])

    assert res.exit_code == 0, res.output
    assert calls == [("post", "http://compute.test:8003/servers/vm1/revertResize", None)]
