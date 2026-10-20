import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "cli"))

from click.testing import CliRunner  # noqa: E402

from cloudx_cli.commands import database  # noqa: E402
from cloudx_cli.main import cli  # noqa: E402

def run(monkeypatch, args):
    calls = []

    def fake(method, url, profile="default", **kw):
        calls.append((method, url.replace(database.DEFAULT_URLS["database"], ""), kw.get("json")))
        return {"items": []}
    monkeypatch.setattr(database, "_api", fake)
    res = CliRunner().invoke(cli, ["-o", "json", "database", *args])
    assert res.exit_code == 0, res.output
    return calls

def test_backup_calls(monkeypatch):
    assert run(monkeypatch, ["backup", "create", "i1", "--name", "b"]) == [("post", "/instances/i1/backups", {"name": "b"})]
    assert run(monkeypatch, ["backup", "list"]) == [("get", "/backups", None)]
    assert run(monkeypatch, ["backup", "delete", "b1"]) == [("delete", "/backups/b1", None)]
    assert run(monkeypatch, ["backup", "restore", "b1", "r1", "--network", "n", "--image", "i", "--flavor", "f"]) == \
        [("post", "/backups/b1/restore", {"name": "r1", "network_id": "n", "image": "i", "flavor": "f"})]
