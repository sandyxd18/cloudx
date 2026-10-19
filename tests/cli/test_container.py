import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "cli"))

from click.testing import CliRunner  # noqa: E402

from cloudx_cli.commands import container as cc  # noqa: E402
from cloudx_cli.main import cli  # noqa: E402

def run(monkeypatch, args):
    calls = []

    def fake(method, url, profile="default", **kw):
        calls.append((method, url.replace(cc.DEFAULT_URLS["container"], ""), kw.get("json")))
        return {"items": [], "logs": "hi"}
    monkeypatch.setattr(cc, "_api", fake)
    res = CliRunner().invoke(cli, ["-o", "json", "container", *args])
    assert res.exit_code == 0, res.output
    return calls

def test_calls(monkeypatch):
    assert run(monkeypatch, ["create", "web", "--image", "nginx", "--network", "n1", "--cpu", "0.5", "--memory", "128",
                             "--env", "A=1=2", "--command", "sleep", "--command", "10"]) == [
        ("post", "/containers", {"name": "web", "image": "nginx", "network_id": "n1", "cpu": 0.5, "memory_mb": 128,
                                 "command": ["sleep", "10"], "env": {"A": "1=2"}, "security_group_ids": []})]
    assert run(monkeypatch, ["restart", "c1"]) == [("post", "/containers/c1/restart", None)]
    assert run(monkeypatch, ["logs", "c1"]) == [("get", "/containers/c1/logs", None)]
    assert run(monkeypatch, ["delete", "c1"]) == [("delete", "/containers/c1", None)]