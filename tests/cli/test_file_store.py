import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "cli"))

from click.testing import CliRunner  # noqa: E402

from cloudx_cli.commands import file_store as fsc  # noqa: E402
from cloudx_cli.main import cli  # noqa: E402

def run(monkeypatch, args):
    calls = []

    def fake(method, url, profile="default", **kw):
        calls.append((method, url.replace(fsc.DEFAULT_URLS["file_store"], ""), kw.get("json")))
        return {"items": []}
    monkeypatch.setattr(fsc, "_api", fake)
    res = CliRunner().invoke(cli, ["-o", "json", "file-store", *args])
    assert res.exit_code == 0, res.output
    return calls

def test_calls(monkeypatch):
    assert run(monkeypatch, ["network", "create", "sn", "--network", "n1"]) == [
        ("post", "/share-networks", {"name": "sn", "network_id": "n1"})]
    assert run(monkeypatch, ["share", "create", "s", "--size", "2", "--network", "sn1"]) == [
        ("post", "/shares", {"name": "s", "size_gb": 2, "share_network_id": "sn1"})]
    assert run(monkeypatch, ["share", "extend", "s1", "5"]) == [("post", "/shares/s1/extend", {"size_gb": 5})]
    assert run(monkeypatch, ["access", "add", "s1", "10.0.0.5", "--level", "ro"]) == [
        ("post", "/shares/s1/access", {"to": "10.0.0.5", "level": "ro"})]
    assert run(monkeypatch, ["access", "remove", "s1", "r1"]) == [("delete", "/shares/s1/access/r1", None)]
    assert run(monkeypatch, ["share", "delete", "s1"]) == [("delete", "/shares/s1", None)]
