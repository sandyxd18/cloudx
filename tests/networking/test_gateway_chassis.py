"""The router gateway must be pinned to a chassis that can really carry external traffic.

networking-api runs as the unprivileged `cloudx` user and cannot read the OVS database socket, so asking OVS for the
local system-id always failed and the code fell back to the first chassis in the Southbound DB. On the lab that was
compute01, which has no provider uplink, so floating IPs never answered ARP. The local chassis is now found by the host
name in the Southbound Chassis table (which also survives a system-id that changed after an Open vSwitch restart), and
only chassis with `enable-chassis-as-gw` and a bridge mapping for the provider network are considered.
"""
import json

import pytest

GW = {"ovn-cms-options": "enable-chassis-as-gw", "ovn-bridge-mappings": "physnet1:br-ex"}
COMPUTE = {"ovn-cms-options": "", "ovn-bridge-mappings": ""}


def chassis_table(*rows):
    """`ovn-sbctl --format=json --columns=name,hostname,other_config find Chassis` for (name, hostname, config) rows."""
    return json.dumps({"headings": ["name", "hostname", "other_config"],
                       "data": [[name, host, ["map", [[k, v] for k, v in config.items()]]] for name, host, config in rows]})


@pytest.fixture
def ovn_cli(monkeypatch):
    from services.networking.app import ovn_cli

    def no_ovs(*args, **kwargs):
        raise AssertionError("the local chassis must not be read from Open vSwitch")

    monkeypatch.setattr(ovn_cli, "ovs", no_ovs)
    return ovn_cli


def pick(ovn_cli, monkeypatch, hostname, *rows):
    monkeypatch.setattr(ovn_cli.socket, "gethostname", lambda: hostname)
    monkeypatch.setattr(ovn_cli, "sbctl", lambda *args: chassis_table(*rows))
    return ovn_cli.get_local_chassis_id()


def test_the_local_gateway_chassis_is_found_by_host_name_even_after_its_system_id_changed(ovn_cli, monkeypatch):
    chosen = pick(ovn_cli, monkeypatch, "infra01",
                  ("compute01", "compute01", COMPUTE),
                  ("488208fc-0000-0000-0000-000000000002", "infra02", GW),
                  ("855053b4-0000-0000-0000-000000000001", "infra01", GW))
    assert chosen == "855053b4-0000-0000-0000-000000000001"


def test_a_local_chassis_without_an_uplink_is_not_chosen_for_the_gateway(ovn_cli, monkeypatch):
    chosen = pick(ovn_cli, monkeypatch, "compute01",
                  ("compute01", "compute01", COMPUTE),
                  ("infra01", "infra01", GW))
    assert chosen == "infra01"


def test_the_first_chassis_in_the_table_is_not_taken_when_it_cannot_be_a_gateway(ovn_cli, monkeypatch):
    chosen = pick(ovn_cli, monkeypatch, "not-a-chassis",
                  ("compute01", "compute01", COMPUTE),
                  ("infra02", "infra02", GW),
                  ("infra01", "infra01", GW))
    assert chosen == "infra01"                       # sorted by host name so the choice is stable


def test_a_chassis_mapped_to_another_provider_network_is_not_a_gateway_for_this_one(ovn_cli, monkeypatch):
    other = {"ovn-cms-options": "enable-chassis-as-gw", "ovn-bridge-mappings": "physnet2:br-ex2"}
    chosen = pick(ovn_cli, monkeypatch, "infra01", ("infra01", "infra01", other), ("infra02", "infra02", GW))
    assert chosen == "infra02"


def test_a_full_host_name_matches_the_short_name_of_the_chassis(ovn_cli, monkeypatch):
    chosen = pick(ovn_cli, monkeypatch, "infra02.lab.example",
                  ("infra01", "infra01", GW), ("infra02", "infra02", GW))
    assert chosen == "infra02"


def test_without_any_gateway_chassis_the_old_choice_is_kept(ovn_cli, monkeypatch):
    # single-node labs that never set enable-chassis-as-gw must keep working: local chassis, else the first one
    assert pick(ovn_cli, monkeypatch, "aio", ("other", "other", COMPUTE), ("aio-uuid", "aio", COMPUTE)) == "aio-uuid"
    assert pick(ovn_cli, monkeypatch, "elsewhere", ("other", "other", COMPUTE), ("aio-uuid", "aio", COMPUTE)) == "other"


def test_no_chassis_at_all_gives_none(ovn_cli, monkeypatch):
    assert pick(ovn_cli, monkeypatch, "infra01") is None
