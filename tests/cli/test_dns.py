import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "cli"))

from click.testing import CliRunner  # noqa: E402

from cloudx_cli.commands import dns as dnscmd  # noqa: E402
from cloudx_cli.main import cli  # noqa: E402


def run(monkeypatch, args, reply=None):
    calls = []

    def fake(method, url, profile="default", **kw):
        calls.append((method, url.replace(dnscmd.DEFAULT_URLS["dns"], ""), kw.get("json")))
        return reply(method, url) if reply else {"zones": [], "recordsets": [], "secondaries": []}
    monkeypatch.setattr(dnscmd, "_api", fake)
    res = CliRunner().invoke(cli, ["-o", "json", "dns", *args])
    assert res.exit_code == 0, res.output
    return calls, res.output


def test_calls(monkeypatch):
    assert run(monkeypatch, ["zone", "create", "example.com", "--internal"])[0] == [
        ("post", "/zones", {"name": "example.com", "ttl": 300, "scope": "internal"})]
    assert run(monkeypatch, ["record", "create", "example.com", "www", "A", "1.2.3.4"])[0] == [
        ("post", "/zones/example.com/recordsets", {"name": "www", "type": "A", "records": ["1.2.3.4"]})]
    assert run(monkeypatch, ["record", "set", "z", "r1", "9.9.9.9", "--ttl", "60"])[0][0][0] == "put"
    assert run(monkeypatch, ["record", "delete", "z", "r1"])[0][0][:2] == ("delete", "/zones/z/recordsets/r1")
    assert run(monkeypatch, ["secondary", "add", "z", "203.0.113.5"])[0][0][2] == {"address": "203.0.113.5"}
    assert run(monkeypatch, ["secondary", "remove", "z", "s1"])[0][0][:2] == ("delete", "/zones/z/secondaries/s1")


def test_zone_show_prints_the_platform_name_server(monkeypatch):
    def reply(method, url):
        if url.endswith("/zones"):
            return {"zones": [{"name": "elb.cloud.lab", "kind": "platform"}, {"name": "cloud.lab", "kind": "platform"}]}
        return {"name": "example.com", "kind": "user", "scope": "public"}
    assert "NS: ns1.cloud.lab" in run(monkeypatch, ["zone", "show", "example.com"], reply)[1]