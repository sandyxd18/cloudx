"""A rule that names a remote group must match on the group's address set, not on `inport == @group`.

On the 8-node lab a TCP connection between two servers of the same group failed when they ran on different compute
nodes (and worked on the same node): ovn-controller expands `@port_group` in an ACL only to the ports bound on the
chassis that evaluates the packet, so `inport == @pg` in a to-lport ACL matched only the destination port itself.
northd keeps two address sets for every port group, `<port group>_ip4` and `<port group>_ip6`, with the addresses of
all its ports on every chassis. They are created by OVN: the networking-api must not create sets with those names, or
the duplicate stops northd ("Transaction causes multiple rows in Address_Set table to have identical values").
"""
from types import SimpleNamespace

import pytest


def rule(**kw):
    base = dict(direction="ingress", ethertype="IPv4", protocol="any", port_min=None, port_max=None,
                remote_ip_prefix=None, remote_group_id="sg-remote", action="allow")
    return SimpleNamespace(**{**base, **kw})


@pytest.fixture
def ovn_cli():
    from services.networking.app import ovn_cli

    return ovn_cli


def test_an_ingress_rule_matches_the_source_in_the_remote_groups_address_set(ovn_cli):
    pg, remote = ovn_cli.port_group_name("sg-own"), ovn_cli.port_group_name("sg-remote")

    assert ovn_cli.acl_match("sg-own", rule()) == f"outport == @{pg} && ip4 && ip4.src == ${remote}_ip4"


def test_an_egress_rule_matches_the_destination_in_the_remote_groups_address_set(ovn_cli):
    pg, remote = ovn_cli.port_group_name("sg-own"), ovn_cli.port_group_name("sg-remote")

    assert ovn_cli.acl_match("sg-own", rule(direction="egress")) == f"inport == @{pg} && ip4 && ip4.dst == ${remote}_ip4"


def test_an_ipv6_rule_uses_the_ip6_address_set(ovn_cli):
    remote = ovn_cli.port_group_name("sg-remote")

    match = ovn_cli.acl_match("sg-own", rule(ethertype="IPv6"))

    assert match.endswith(f"&& ip6.src == ${remote}_ip6") and "ip4" not in match


def test_the_protocol_and_port_stay_in_the_match(ovn_cli):
    remote = ovn_cli.port_group_name("sg-remote")

    match = ovn_cli.acl_match("sg-own", rule(protocol="tcp", port_min="6443", port_max="6443"))

    assert " && tcp && tcp.dst == 6443 && ip4.src == $" + remote + "_ip4" in match


def test_a_rule_that_names_its_own_group_matches_its_own_address_set(ovn_cli):
    own = ovn_cli.port_group_name("sg-own")

    assert ovn_cli.acl_match("sg-own", rule(remote_group_id="sg-own")).endswith(f"&& ip4.src == ${own}_ip4")


def test_no_rule_refers_to_the_remote_group_by_port(ovn_cli):
    for direction in ("ingress", "egress"):
        match = ovn_cli.acl_match("sg-own", rule(direction=direction))
        assert "@" + ovn_cli.port_group_name("sg-remote") not in match
        assert match.count("inport") + match.count("outport") == 1     # only the group's own side

