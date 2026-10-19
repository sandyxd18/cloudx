import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "cli"))

from click.testing import CliRunner  # noqa: E402

from cloudx_cli.commands import ha  # noqa: E402
from cloudx_cli.main import cli  # noqa: E402

def run(monkeypatch, args):
    calls = []

    def fake(method, url, profile="default", **kw):
        calls.append((method, url.replace(ha.DEFAULT_URLS["ha"], "")))
        return {"items": []}
    monkeypatch.setattr(ha, "_api", fake)
    res = CliRunner().invoke(cli, ["-o", "json", "ha", *args])
    assert res.exit_code == 0, res.output
    return calls

def test_calls(monkeypatch):
    assert run(monkeypatch, ["protect", "s1"]) == [("put", "/protected/s1")]
    assert run(monkeypatch, ["unprotect", "s1"]) == [("delete", "/protected/s1")]
    assert run(monkeypatch, ["hosts"]) == [("get", "/hosts")]
    assert run(monkeypatch, ["reset", "h1"]) == [("post", "/hosts/h1/reset")]
    assert run(monkeypatch, ["recoveries"]) == [("get", "/recoveries")]