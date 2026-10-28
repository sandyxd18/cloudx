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
        return {"status": "ok"}

    monkeypatch.setattr(compute, "_api", fake_api)
    monkeypatch.setattr(compute, "DEFAULT_URLS", {"compute": "http://compute.test:8003", "volume": "http://block-store.test:8004"})
    return calls


def test_server_volume_attach_goes_through_block_store(monkeypatch):
    # the compute endpoint also needs the volume path and host, which only block-store knows; sending just volume_id gave HTTP 422
    calls = _capture(monkeypatch)

    res = CliRunner().invoke(cli, ["server", "volume", "attach", "vm1", "--volume", "vol1"])

    assert res.exit_code == 0, res.output
    assert calls == [("post", "http://block-store.test:8004/volumes/vol1/attach", {"server": "vm1", "server_id": "vm1"})]


def test_server_volume_detach_goes_through_block_store(monkeypatch):
    calls = _capture(monkeypatch)

    res = CliRunner().invoke(cli, ["server", "volume", "detach", "vm1", "--volume", "vol1"])

    assert res.exit_code == 0, res.output
    assert [(m, u) for m, u, _ in calls] == [("post", "http://block-store.test:8004/volumes/vol1/detach")]
