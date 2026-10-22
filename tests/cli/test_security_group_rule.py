import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "cli"))

from click.testing import CliRunner  # noqa: E402

from cloudx_cli.commands import network  # noqa: E402
from cloudx_cli.main import cli  # noqa: E402


def test_rule_list_shows_the_ports_the_api_returns(monkeypatch):
    # the API lists rules with the column names port_min/port_max; the table asked for port_range_* and showed blanks
    rules = [{"id": "r1", "direction": "ingress", "protocol": "tcp", "port_min": "8080", "port_max": "8090",
              "remote_ip_prefix": "0.0.0.0/0"}]
    monkeypatch.setattr(network, "_api", lambda method, url, profile="default", **kw: {"items": rules})

    res = CliRunner().invoke(cli, ["security-group", "rule", "list", "sg1"])

    assert res.exit_code == 0, res.output
    assert "8080" in res.output and "8090" in res.output


def test_rule_add_sends_the_port_names_the_api_schema_knows(monkeypatch):
    sent = []
    monkeypatch.setattr(network, "_api", lambda method, url, profile="default", **kw: sent.append(kw["json"]) or {})

    runner = CliRunner()
    assert runner.invoke(cli, ["security-group", "rule", "add", "sg1", "--port", "22"]).exit_code == 0
    assert runner.invoke(cli, ["security-group", "rule", "add", "sg1", "--port-range-min", "8000",
                               "--port-range-max", "8100"]).exit_code == 0

    assert (sent[0]["port_min"], sent[0]["port_max"]) == (22, 22)
    assert (sent[1]["port_min"], sent[1]["port_max"]) == (8000, 8100)
    assert not any("port_range_min" in payload or "port_range_max" in payload for payload in sent)
