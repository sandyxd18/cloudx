import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "cli"))

from click.testing import CliRunner  # noqa: E402

from cloudx_cli.commands import compute  # noqa: E402
from cloudx_cli.main import cli  # noqa: E402


def _capture(monkeypatch):
    calls = []

    def fake_api(method, url, profile="default", **kw):
        calls.append((method, url, kw.get("json")))
        return {"id": "s1", "name": "vm"}

    monkeypatch.setattr(compute, "_api", fake_api)
    monkeypatch.setattr(compute, "DEFAULT_URLS", {"compute": "http://compute.test:8003"})
    return calls


def test_ipv6_flag_asks_for_an_ipv6_address(monkeypatch):
    calls = _capture(monkeypatch)
    res = CliRunner().invoke(cli, ["server", "create", "vm", "--image", "i", "--network", "n", "--ipv6"])
    assert res.exit_code == 0, res.output
    assert calls[0][2]["ipv6"] is True


def test_without_the_flag_the_request_does_not_mention_ipv6(monkeypatch):
    calls = _capture(monkeypatch)
    res = CliRunner().invoke(cli, ["server", "create", "vm", "--image", "i", "--network", "n"])
    assert res.exit_code == 0, res.output
    assert "ipv6" not in calls[0][2]


# ── several --network: the first is the primary one (`network`), the rest are the extra NICs (`networks`) ──

def _create(*networks, extra=()):
    args = ["server", "create", "vm", "--image", "i"]
    for n in networks:
        args += ["--network", n]
    return CliRunner().invoke(cli, [*args, *extra])


def test_the_first_network_is_the_primary_and_the_others_are_extra_nics(monkeypatch):
    calls = _capture(monkeypatch)
    res = _create("net-a", "net-b")
    assert res.exit_code == 0, res.output
    assert calls[0][2]["network"] == "net-a"
    assert calls[0][2]["networks"] == ["net-b"]


def test_the_order_given_is_kept_for_three_networks(monkeypatch):
    calls = _capture(monkeypatch)
    res = _create("net-a", "net-c", "net-b")
    assert res.exit_code == 0, res.output
    assert calls[0][2]["network"] == "net-a"
    assert calls[0][2]["networks"] == ["net-c", "net-b"]


def test_one_network_sends_the_same_request_as_before(monkeypatch):
    calls = _capture(monkeypatch)
    res = _create("net-a")
    assert res.exit_code == 0, res.output
    assert calls[0][2]["network"] == "net-a"
    assert "networks" not in calls[0][2]


def test_the_same_network_twice_is_refused_before_anything_is_sent(monkeypatch):
    # the API drops a repeated network without a word, so the caller would get one NIC while asking for two
    calls = _capture(monkeypatch)
    res = _create("net-a", "net-b", "net-a")
    assert res.exit_code != 0
    assert "net-a" in res.output and "twice" in res.output
    assert calls == []


def test_a_network_is_still_required(monkeypatch):
    calls = _capture(monkeypatch)
    res = _create()
    assert res.exit_code != 0
    assert calls == []


def test_ipv6_and_several_networks_go_together(monkeypatch):
    calls = _capture(monkeypatch)
    res = _create("net-a", "net-b", extra=("--ipv6",))
    assert res.exit_code == 0, res.output
    assert calls[0][2]["ipv6"] is True and calls[0][2]["networks"] == ["net-b"]
