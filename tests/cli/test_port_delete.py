import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "cli"))

from click.testing import CliRunner  # noqa: E402

from cloudx_cli.commands import network  # noqa: E402
from cloudx_cli.main import cli  # noqa: E402


def _capture(monkeypatch, port, servers=()):
    calls = []

    def fake_api(method, url, profile="default", **kw):
        calls.append((method, url))
        if url.endswith("/servers"):
            return {"items": list(servers)}
        return port if method == "get" else {"status": "deleted"}

    monkeypatch.setattr(network, "_api", fake_api)
    monkeypatch.setattr(network, "DEFAULT_URLS", {"network": "http://net.test:8001", "compute": "http://compute.test:8003"})
    return calls


def test_a_port_nothing_uses_is_deleted(monkeypatch):
    calls = _capture(monkeypatch, {"id": "p1", "name": "spare", "device_id": None})

    res = CliRunner().invoke(cli, ["port", "delete", "p1"])

    assert res.exit_code == 0, res.output
    assert ("delete", "http://net.test:8001/ports/p1") in calls
    assert "p1" in res.output


def test_a_port_that_a_server_uses_is_refused_and_left_alone(monkeypatch):
    # deleting it drops the logical port under a running server; `server port remove` detaches it first
    calls = _capture(monkeypatch, {"id": "p1", "name": "nic2", "device_id": "srv-1", "device_owner": "compute:server"})

    res = CliRunner().invoke(cli, ["port", "delete", "p1"])

    assert res.exit_code != 0
    assert "srv-1" in res.output and "server port remove" in res.output
    assert not [c for c in calls if c[0] == "delete"]


def test_force_deletes_a_port_that_is_in_use(monkeypatch):
    calls = _capture(monkeypatch, {"id": "p1", "name": "nic2", "device_id": "srv-1"})

    res = CliRunner().invoke(cli, ["port", "delete", "p1", "--force"])

    assert res.exit_code == 0, res.output
    assert ("delete", "http://net.test:8001/ports/p1") in calls


# `server port add --network` makes the port with no device_id and the compute API does not set one when it attaches the
# port, so the device_id guard let through exactly the port a user adds to a running server: deleting it dropped the logical
# port under the server and left the dead id in its record. The servers' own records say which ports they use.

def test_a_port_in_a_servers_extra_ports_is_refused_even_without_a_device_id(monkeypatch):
    calls = _capture(monkeypatch, {"id": "p9", "name": "vm1-port", "device_id": None},
                     servers=[{"id": "srv-1", "name": "vm1", "port_id": "p1", "extra_port_ids": "p5,p9"}])

    res = CliRunner().invoke(cli, ["port", "delete", "p9"])

    assert res.exit_code != 0 and "vm1" in res.output and "server port remove" in res.output
    assert not [c for c in calls if c[0] == "delete"]


def test_a_servers_primary_port_is_refused_too(monkeypatch):
    calls = _capture(monkeypatch, {"id": "p1", "device_id": None}, servers=[{"id": "srv-1", "name": "vm1", "port_id": "p1", "extra_port_ids": None}])

    res = CliRunner().invoke(cli, ["port", "delete", "p1"])

    assert res.exit_code != 0 and not [c for c in calls if c[0] == "delete"]


def test_a_port_that_a_router_or_another_service_owns_is_refused(monkeypatch):
    calls = _capture(monkeypatch, {"id": "p2", "device_id": None, "device_owner": "network:router_interface"})

    res = CliRunner().invoke(cli, ["port", "delete", "p2"])

    assert res.exit_code != 0 and "network:router_interface" in res.output
    assert not [c for c in calls if c[0] == "delete"]


def test_a_port_that_no_server_lists_is_deleted(monkeypatch):
    calls = _capture(monkeypatch, {"id": "p9", "device_id": None},
                     servers=[{"id": "srv-1", "name": "vm1", "port_id": "p1", "extra_port_ids": "p5"}])

    res = CliRunner().invoke(cli, ["port", "delete", "p9"])

    assert res.exit_code == 0, res.output
    assert ("delete", "http://net.test:8001/ports/p9") in calls


def test_force_skips_the_server_lookup_as_well(monkeypatch):
    calls = _capture(monkeypatch, {"id": "p9", "device_id": None}, servers=[{"id": "srv-1", "name": "vm1", "port_id": "p1", "extra_port_ids": "p9"}])

    res = CliRunner().invoke(cli, ["port", "delete", "p9", "--force"])

    assert res.exit_code == 0, res.output
    assert ("delete", "http://net.test:8001/ports/p9") in calls
