import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "cli"))

from click.testing import CliRunner  # noqa: E402

from cloudx_cli.commands import reservation as rc  # noqa: E402
from cloudx_cli.main import cli  # noqa: E402

def test_calls(monkeypatch):
    calls = []

    def fake(method, url, profile="default", **kw):
        calls.append((method, url.replace(rc.DEFAULT_URLS["reservation"], ""), kw.get("json"), kw.get("params")))
        return {"items": []}
    monkeypatch.setattr(rc, "_api", fake)
    for args in (["create", "l1", "--flavor", "small", "--amount", "2", "--start", "2030-01-01T00:00", "--end", "2030-01-02T00:00"], ["capacity", "--start", "2030-01-01"], ["delete", "l1"]):
        res = CliRunner().invoke(cli, ["-o", "json", "reservation", *args])
        assert res.exit_code == 0, res.output
    assert calls == [
        ("post", "/leases", {"name": "l1", "flavor": "small", "amount": 2, "start_at": "2030-01-01T00:00", "end_at": "2030-01-02T00:00"}, None),
        ("get", "/capacity", None, {"start": "2030-01-01"}),
        ("delete", "/leases/l1", None, None),
    ]
