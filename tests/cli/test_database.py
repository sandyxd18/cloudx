import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "cli"))

from click.testing import CliRunner  # noqa: E402

from cloudx_cli.commands import database as dbc  # noqa: E402
from cloudx_cli.main import cli  # noqa: E402

def run(monkeypatch, args):
    calls = []

    def fake(method, url, profile="default", **kw):
        calls.append((method, url.replace(dbc.DEFAULT_URLS["database"], ""), kw.get("json")))
        return {"items": []}
    monkeypatch.setattr(dbc, "_api", fake)
    res = CliRunner().invoke(cli, ["-o", "json", "database", *args])
    assert res.exit_code == 0, res.output
    return calls

def test_calls(monkeypatch):
    assert run(monkeypatch, ["create", "db1", "--engine", "postgresql", "--backend", "vm", "--network", "n1", "--image", "ubuntu",
                             "--flavor", "small", "--storage", "block", "--size", "5", "--allowed-cidr", "10.0.0.0/24"]) == [
        ("post", "/instances", {"name": "db1", "engine": "postgresql", "backend": "vm", "network_id": "n1", "storage": "block",
                                "size_gb": 5, "image": "ubuntu", "flavor": "small", "allowed_cidr": "10.0.0.0/24"})]
    assert run(monkeypatch, ["create", "c", "--engine", "mariadb", "--backend", "container", "--network", "n1"]) == [
        ("post", "/instances", {"name": "c", "engine": "mariadb", "backend": "container", "network_id": "n1", "storage": "local"})]
    assert run(monkeypatch, ["stop", "d1"]) == [("post", "/instances/d1/stop", None)]
    assert run(monkeypatch, ["delete", "d1"]) == [("delete", "/instances/d1", None)]