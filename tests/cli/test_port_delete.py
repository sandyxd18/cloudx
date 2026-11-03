import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "cli"))

from click.testing import CliRunner  # noqa: E402

from cloudx_cli.commands import network  # noqa: E402
from cloudx_cli.main import cli  # noqa: E402


def _capture(monkeypatch, port):
    calls = []

    def fake_api(method, url, profile="default", **kw):
        calls.append((method, url))
        return port if method == "get" else {"status": "deleted"}

    monkeypatch.setattr(network, "_api", fake_api)
    monkeypatch.setattr(network, "DEFAULT_URLS", {"network": "http://net.test:8001"})
    return calls


def test_a_port_nothing_uses_is_deleted(monkeypatch):
    calls = _capture(monkeypatch, {"id": "p1", "name": "spare", "device_id": None})

    res = CliRunner().invoke(cli, ["port", "delete", "p1"])

    assert res.exit_code == 0, res.output
    assert ("delete", "http://net.test:8001/ports/p1") in calls
    assert "p1" in res.output


def test_a_port_that_a_server_uses_is_refused_and_left_alone(monkeypatch):
    # deleting it drops the logical port under a running server; `server port remove` detaches it first
    calls = _capture(monkeypatch, {"id": "p1", "name": "nic2", "device_id": "srv-1", "device_owner": "compute:server"})

    res = CliRunner().invoke(cli, ["port", "delete", "p1"])

    assert res.exit_code != 0
    assert "srv-1" in res.output and "server port remove" in res.output
    assert not [c for c in calls if c[0] == "delete"]


def test_force_deletes_a_port_that_is_in_use(monkeypatch):
    calls = _capture(monkeypatch, {"id": "p1", "name": "nic2", "device_id": "srv-1"})

    res = CliRunner().invoke(cli, ["port", "delete", "p1", "--force"])

    assert res.exit_code == 0, res.output
    assert ("delete", "http://net.test:8001/ports/p1") in calls
