import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "cli"))

from click.testing import CliRunner  # noqa: E402

from cloudx_cli.commands import queue as qc  # noqa: E402
from cloudx_cli.main import cli  # noqa: E402

def test_calls(monkeypatch):
    calls = []

    def fake(method, url, profile="default", **kw):
        calls.append((method, url.replace(qc.DEFAULT_URLS["messaging"], ""), kw.get("json")))
        return {"items": [], "claim_id": "c1"}
    monkeypatch.setattr(qc, "_api", fake)
    for args in (["create", "jobs"], ["post", "jobs", '{"a":1}'], ["post", "jobs", "hello", "--ttl", "90"], ["claim", "jobs"], ["ack", "jobs", "7", "--claim", "c1"]):
        res = CliRunner().invoke(cli, ["-o", "json", "queue", *args])
        assert res.exit_code == 0, res.output
    assert calls == [
        ("put", "/queues/jobs", None),
        ("post", "/queues/jobs/messages", {"messages": [{"body": {"a": 1}, "ttl": 3600}]}),
        ("post", "/queues/jobs/messages", {"messages": [{"body": "hello", "ttl": 90}]}),
        ("post", "/queues/jobs/claims?ttl=300&limit=10", None),
        ("delete", "/queues/jobs/messages/7?claim_id=c1", None),
    ]
