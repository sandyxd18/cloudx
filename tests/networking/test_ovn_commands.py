
def test_networking_ovn_command_builders():
    from services.networking.app import ovn_cli as net_main
    from services.networking.app.models import SecurityGroupRule

    dhcp = net_main.dhcp_options_args("10.10.0.0/24", "10.10.0.1", ["1.1.1.1"])
    assert dhcp[:2] == ["create", "DHCP_Options"]
    assert "options:router=10.10.0.1" in dhcp
    assert "options:dns_server={1.1.1.1}" in dhcp

    assert net_main.nat_rule_args("router1", "192.168.100.10", "10.10.0.2") == [
        "--may-exist",
        "lr-nat-add",
        "router1",
        "dnat_and_snat",
        "192.168.100.10",
        "10.10.0.2",
    ]

    rule = SecurityGroupRule(
        security_group_id="sg-id",
        direction="ingress",
        protocol="tcp",
        port_min="22",
        port_max="22",
        remote_ip_prefix="0.0.0.0/0",
    )
    match = net_main.acl_match("sg-id", rule)
    assert f"outport == @{net_main.port_group_name('sg-id')}" in match
    assert "tcp.dst == 22" in match
    assert "ip4.src == 0.0.0.0/0" in match
    assert net_main.acl_priority(rule) == "2002"

    egress = SecurityGroupRule(security_group_id="sg-id", direction="egress", protocol="any", remote_ip_prefix="0.0.0.0/0")
    assert f"inport == @{net_main.port_group_name('sg-id')}" in net_main.acl_match("sg-id", egress)

    localnet = net_main.localnet_commands("public-net", "physnet1")
    assert ["lsp-set-type", "public-net-localnet", "localnet"] in localnet
    assert ["lsp-set-options", "public-net-localnet", "network_name=physnet1"] in localnet
    assert net_main.external_network_defaults("public-net")["provider_bridge"]
