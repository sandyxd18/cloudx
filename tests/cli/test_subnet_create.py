import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "cli"))

from click.testing import CliRunner  # noqa: E402

from cloudx_cli.commands import network  # noqa: E402
from cloudx_cli.main import cli  # noqa: E402


def _capture(monkeypatch):
    calls = []

    def fake_api(method, url, profile="default", **kw):
        calls.append((method, url, kw.get("json")))
        return {"name": "s"}

    monkeypatch.setattr(network, "_api", fake_api)
    monkeypatch.setattr(network, "DEFAULT_URLS", {"network": "http://net.test:8001"})
    return calls


def test_an_ipv6_subnet_can_set_both_modes_and_needs_no_gateway(monkeypatch):
    calls = _capture(monkeypatch)
    res = CliRunner().invoke(cli, ["subnet", "create", "s6", "--network", "n", "--cidr", "fd42:7:7::/64", "--ip-version", "6",
                                   "--ipv6-ra-mode", "dhcpv6-stateful", "--ipv6-address-mode", "dhcpv6-stateful"])
    assert res.exit_code == 0, res.output
    assert calls == [("post", "http://net.test:8001/subnets", {
        "name": "s6", "network": "n", "cidr": "fd42:7:7::/64", "enable_dhcp": True, "ip_version": 6,
        "ipv6_ra_mode": "dhcpv6-stateful", "ipv6_address_mode": "dhcpv6-stateful"})]


def test_the_ip_version_follows_the_cidr_unless_given(monkeypatch):
    calls = _capture(monkeypatch)
    for cidr, extra in (("fd42:7:7::/64", []), ("10.0.0.0/24", ["--gateway", "10.0.0.1"]), ("10.0.0.0/24", ["--ip-version", "4", "--gateway", "10.0.0.1"])):
        assert CliRunner().invoke(cli, ["subnet", "create", "s", "--network", "n", "--cidr", cidr, *extra]).exit_code == 0
    assert [c[2]["ip_version"] for c in calls] == [6, 4, 4]


def test_the_modes_are_checked_by_the_cli():
    res = CliRunner().invoke(cli, ["subnet", "create", "s6", "--network", "n", "--cidr", "fd42::/64", "--ipv6-ra-mode", "bogus"])
    assert res.exit_code != 0 and "bogus" in res.output


def test_dns_nameservers_go_out_as_a_list(monkeypatch):
    # the API field is list[str]; a comma-joined string was answered with HTTP 422
    calls = _capture(monkeypatch)
    res = CliRunner().invoke(cli, ["subnet", "create", "s4", "--network", "n", "--cidr", "10.0.0.0/24", "--gateway", "10.0.0.1",
                                   "--dns-nameserver", "1.1.1.1", "--dns-nameserver", "8.8.8.8"])
    assert res.exit_code == 0, res.output
    assert calls[0][2]["dns_nameservers"] == ["1.1.1.1", "8.8.8.8"]
    assert "ipv6_ra_mode" not in calls[0][2]
