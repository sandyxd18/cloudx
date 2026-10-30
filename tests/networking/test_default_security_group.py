"""The default security group carried IPv4 rules only, so a port with an IPv6 address could not send or receive IPv6."""
import pytest


@pytest.fixture
def boot(networking, monkeypatch):
    calls = []
    monkeypatch.setattr(networking.ovn_cli, "ovn", lambda *a, **k: calls.append(a) or "")

    def run():
        networking.bootstrap.bootstrap_default_security_group()

    def rules():
        with networking.db.SessionLocal() as db:
            return sorted((r.ethertype, r.direction, r.protocol, r.port_min, r.remote_ip_prefix)
                          for r in db.query(networking.models.SecurityGroupRule))

    return networking, run, rules, calls


V4 = [("IPv4", "egress", "any", None, "0.0.0.0/0"), ("IPv4", "ingress", "icmp", None, "0.0.0.0/0"), ("IPv4", "ingress", "tcp", "22", "0.0.0.0/0")]
V6 = [("IPv6", "egress", "any", None, "::/0"), ("IPv6", "ingress", "icmp", None, "::/0"), ("IPv6", "ingress", "tcp", "22", "::/0")]


def test_the_default_group_has_the_same_rules_for_both_families(boot):
    _, run, rules, _ = boot
    run()
    assert rules() == sorted(V4 + V6)


def test_a_second_start_adds_nothing(boot):
    _, run, rules, _ = boot
    run()
    run()
    assert len(rules()) == 6


def test_a_default_group_made_before_gets_the_ipv6_rules_on_the_next_start(boot):
    networking, run, rules, _ = boot
    with networking.db.SessionLocal() as db:
        group = networking.models.SecurityGroup(name="default", description="old", project_id="default")
        db.add(group)
        db.flush()
        for ethertype, direction, protocol, port, prefix in V4:
            db.add(networking.models.SecurityGroupRule(security_group_id=group.id, direction=direction, protocol=protocol,
                                                       port_min=port, port_max=port, remote_ip_prefix=prefix, action="allow"))
        db.commit()
    run()
    assert rules() == sorted(V4 + V6)


def test_the_ssh_and_icmp_switches_apply_to_both_families(boot, monkeypatch):
    networking, run, rules, _ = boot
    monkeypatch.setattr(networking.bootstrap, "DEFAULT_SECURITY_GROUP_ALLOW_SSH", False)
    monkeypatch.setattr(networking.bootstrap, "DEFAULT_SECURITY_GROUP_ALLOW_ICMP", False)
    run()
    assert rules() == [V4[0], V6[0]]


def _matches(calls):
    """The match string of every ACL added to a port group: acl-add PG DIRECTION PRIORITY MATCH ACTION."""
    return [a[6] for a in calls if a[:3] == ("--type=port-group", "--may-exist", "acl-add")]


def test_the_acls_for_ipv6_use_plain_ip6_and_icmp6(boot):
    # `ip6.dst == ::/0` adds nothing to `ip6`, and OVN drops an ACL it cannot compile without saying so
    _, run, _, calls = boot
    run()
    matches = _matches(calls)
    assert not [m for m in matches if "::/0" in m]
    for expected in (
        lambda m: m.startswith("inport ==") and m.endswith("&& ip6"),                               # egress, any protocol
        lambda m: m.startswith("outport ==") and m.endswith("&& ip6 && tcp && tcp.dst == 22"),     # ssh
        lambda m: m.startswith("outport ==") and m.endswith("&& ip6 && icmp6"),                    # ICMPv6
    ):
        assert sum(1 for m in matches if expected(m)) == 1, matches
    assert any(m.endswith("&& ip4 && icmp4 && ip4.src == 0.0.0.0/0") for m in matches)            # IPv4 as before
    assert any(m.endswith("&& ip4 && tcp && tcp.dst == 22 && ip4.src == 0.0.0.0/0") for m in matches)


def test_a_real_ipv6_prefix_keeps_its_clause(networking):
    group = networking.models.SecurityGroup(id="g1", name="g")
    rule = lambda prefix: networking.models.SecurityGroupRule(  # noqa: E731
        security_group_id="g1", direction="ingress", ethertype="IPv6", protocol="tcp", port_min="22", port_max="22", remote_ip_prefix=prefix)
    assert networking.ovn_cli.acl_match(group.id, rule("fd42:7:7::/64")).endswith("&& ip6.src == fd42:7:7::/64")
    assert networking.ovn_cli.acl_match(group.id, rule("::/0")).endswith("&& tcp.dst == 22")
    assert networking.ovn_cli.acl_match(group.id, rule("not-a-prefix")).endswith("&& ip6.src == not-a-prefix")
