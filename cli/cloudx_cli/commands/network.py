import sys

import click

from cloudx_cli.app import cli
from cloudx_cli.common import DEFAULT_URLS, _api, _output


# ═══════════════════════════════════════════════════════════════════════════════
# NETWORK
# ═══════════════════════════════════════════════════════════════════════════════
@cli.group()
@click.pass_context
def network(ctx):
    """Manage networks"""


@network.command("create")
@click.argument("name")
@click.option("--external", is_flag=True)
@click.option("--shared", is_flag=True)
@click.pass_context
def network_create(ctx, name, external, shared):
    data = _api("post", f"{DEFAULT_URLS['network']}/networks", ctx.obj["profile"],
                json={"name": name, "external": external, "shared": shared})
    _output(data, ctx.obj["fmt"])


@network.command("list")
@click.pass_context
def network_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['network']}/networks", ctx.obj["profile"])
    _output(data.get("items", []), ctx.obj["fmt"], ["id", "name", "status", "router:external", "admin_state_up", "mtu"])


@network.command("show")
@click.argument("ref")
@click.pass_context
def network_show(ctx, ref):
    data = _api("get", f"{DEFAULT_URLS['network']}/networks/{ref}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])


@network.command("delete")
@click.argument("ref")
@click.pass_context
def network_delete(ctx, ref):
    _api("delete", f"{DEFAULT_URLS['network']}/networks/{ref}", ctx.obj["profile"])
    click.echo(f"✓ Network {ref} deleted")


# ═══════════════════════════════════════════════════════════════════════════════
# SUBNET
# ═══════════════════════════════════════════════════════════════════════════════
@cli.group()
@click.pass_context
def subnet(ctx):
    """Manage subnets"""


@subnet.command("create")
@click.argument("name")
@click.option("--network", required=True)
@click.option("--cidr", required=True)
@click.option("--gateway")
@click.option("--ip-version", type=click.Choice(["4", "6"]), default=None, help="Default: taken from --cidr")
@click.option("--dhcp/--no-dhcp", default=True)
@click.option("--dns-nameserver", "dns", multiple=True)
@click.option("--allocation-start")
@click.option("--allocation-end")
@click.option("--ipv6-ra-mode", type=click.Choice(["slaac", "dhcpv6-stateful", "dhcpv6-stateless"]),
              help="IPv6 only: how routers advertise the prefix")
@click.option("--ipv6-address-mode", type=click.Choice(["slaac", "dhcpv6-stateful", "dhcpv6-stateless"]),
              help="IPv6 only: how instances get their address")
@click.pass_context
def subnet_create(ctx, name, network, cidr, gateway, ip_version, dhcp, dns, allocation_start, allocation_end,
                  ipv6_ra_mode, ipv6_address_mode):
    """Create a subnet. --gateway is required for IPv4; an IPv6 subnet may go without one."""
    payload = {"name": name, "network": network, "cidr": cidr, "enable_dhcp": dhcp,
               "ip_version": int(ip_version or (6 if ":" in cidr else 4))}
    if ipv6_ra_mode:      payload["ipv6_ra_mode"] = ipv6_ra_mode
    if ipv6_address_mode: payload["ipv6_address_mode"] = ipv6_address_mode
    if gateway:           payload["gateway"] = gateway
    if dns:               payload["dns_nameservers"] = list(dns)  # the API field is a list; a joined string was a 422
    if allocation_start:  payload["allocation_start"] = allocation_start
    if allocation_end:    payload["allocation_end"] = allocation_end
    data = _api("post", f"{DEFAULT_URLS['network']}/subnets", ctx.obj["profile"], json=payload)
    _output(data, ctx.obj["fmt"])


@subnet.command("list")
@click.pass_context
def subnet_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['network']}/subnets", ctx.obj["profile"])
    _output(data.get("items", []), ctx.obj["fmt"], ["id", "name", "cidr", "gateway_ip", "network_id", "ip_version"])


@subnet.command("show")
@click.argument("ref")
@click.pass_context
def subnet_show(ctx, ref):
    data = _api("get", f"{DEFAULT_URLS['network']}/subnets/{ref}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])


@subnet.command("delete")
@click.argument("ref")
@click.pass_context
def subnet_delete(ctx, ref):
    _api("delete", f"{DEFAULT_URLS['network']}/subnets/{ref}", ctx.obj["profile"])
    click.echo(f"✓ Subnet {ref} deleted")


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTER
# ═══════════════════════════════════════════════════════════════════════════════
@cli.group()
@click.pass_context
def router(ctx):
    """Manage routers"""


@router.command("create")
@click.argument("name")
@click.pass_context
def router_create(ctx, name):
    data = _api("post", f"{DEFAULT_URLS['network']}/routers", ctx.obj["profile"],
                json={"name": name})
    _output(data, ctx.obj["fmt"])


@router.command("list")
@click.pass_context
def router_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['network']}/routers", ctx.obj["profile"])
    _output(data.get("items", []), ctx.obj["fmt"], ["id", "name", "status", "admin_state_up"])


@router.command("show")
@click.argument("ref")
@click.pass_context
def router_show(ctx, ref):
    data = _api("get", f"{DEFAULT_URLS['network']}/routers/{ref}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])


@router.command("delete")
@click.argument("ref")
@click.pass_context
def router_delete(ctx, ref):
    _api("delete", f"{DEFAULT_URLS['network']}/routers/{ref}", ctx.obj["profile"])
    click.echo(f"✓ Router {ref} deleted")


@router.command("set-gateway")
@click.argument("ref")
@click.argument("network")
@click.pass_context
def router_set_gateway(ctx, ref, network):
    _api("post", f"{DEFAULT_URLS['network']}/routers/{ref}/gateway",
         ctx.obj["profile"], json={"external_network": network})
    click.echo(f"✓ Gateway set on router {ref}")


@router.command("add-interface")
@click.argument("ref")
@click.option("--subnet", required=True)
@click.pass_context
def router_add_interface(ctx, ref, subnet):
    _api("post", f"{DEFAULT_URLS['network']}/routers/{ref}/interfaces",
         ctx.obj["profile"], json={"subnet": subnet})
    click.echo(f"✓ Interface added to router {ref}")


@router.command("remove-interface")
@click.argument("ref")
@click.argument("subnet")
@click.pass_context
def router_remove_interface(ctx, ref, subnet):
    _api("delete", f"{DEFAULT_URLS['network']}/routers/{ref}/interfaces/{subnet}",
         ctx.obj["profile"])
    click.echo(f"✓ Interface removed from router {ref}")


# ═══════════════════════════════════════════════════════════════════════════════
# PORT
# ═══════════════════════════════════════════════════════════════════════════════
@cli.group()
@click.pass_context
def port(ctx):
    """Manage network ports"""


@port.command("list")
@click.pass_context
def port_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['network']}/ports", ctx.obj["profile"])
    _output(data.get("items", []), ctx.obj["fmt"], ["id", "name", "fixed_ip", "mac_address", "status"])


@port.command("show")
@click.argument("ref")
@click.pass_context
def port_show(ctx, ref):
    data = _api("get", f"{DEFAULT_URLS['network']}/ports/{ref}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])


@port.command("set-security-group")
@click.argument("ref")
@click.option("--security-group", "sgs", multiple=True, required=True)
@click.pass_context
def port_set_sg(ctx, ref, sgs):
    _api("post", f"{DEFAULT_URLS['network']}/ports/{ref}/security-groups",
         ctx.obj["profile"], json={"security_groups": list(sgs)})
    click.echo(f"✓ Security groups updated on port {ref}")


@port.command("clear-security-groups")
@click.argument("ref")
@click.pass_context
def port_clear_sg(ctx, ref):
    _api("post", f"{DEFAULT_URLS['network']}/ports/{ref}/security-groups/clear",
         ctx.obj["profile"])
    click.echo(f"✓ Security groups cleared on port {ref}")


# ═══════════════════════════════════════════════════════════════════════════════
# FLOATING IP
# ═══════════════════════════════════════════════════════════════════════════════
@cli.group("floating-ip")
@click.pass_context
def floating_ip(ctx):
    """Manage floating IPs"""


@floating_ip.command("create")
@click.argument("network")
@click.pass_context
def fip_create(ctx, network):
    data = _api("post", f"{DEFAULT_URLS['network']}/floating-ips", ctx.obj["profile"],
                json={"network": network})
    _output(data, ctx.obj["fmt"])


@floating_ip.command("list")
@click.pass_context
def fip_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['network']}/floating-ips", ctx.obj["profile"])
    _output(data.get("items", []), ctx.obj["fmt"], ["id", "floating_ip_address", "fixed_ip_address", "status", "router_id"])


@floating_ip.command("associate")
@click.argument("ip")
@click.option("--server", required=True)
@click.option("--router", required=True)
@click.pass_context
def fip_associate(ctx, ip, server, router):
    port = _api("get", f"{DEFAULT_URLS['compute']}/servers/{server}",
                ctx.obj["profile"]).get("port_id")
    if not port:
        click.echo(f"❌ No port found for server '{server}'", err=True)
        sys.exit(1)
    _api("post", f"{DEFAULT_URLS['network']}/floating-ips/{ip}/associate",
         ctx.obj["profile"], json={"server": server, "port": port, "router": router})
    click.echo(f"✓ Floating IP {ip} associated")


@floating_ip.command("disassociate")
@click.argument("ip")
@click.pass_context
def fip_disassociate(ctx, ip):
    _api("post", f"{DEFAULT_URLS['network']}/floating-ips/{ip}/disassociate",
         ctx.obj["profile"])
    click.echo(f"✓ Floating IP {ip} disassociated")


@floating_ip.command("show")
@click.argument("ip")
@click.pass_context
def fip_show(ctx, ip):
    data = _api("get", f"{DEFAULT_URLS['network']}/floating-ips/{ip}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])


@floating_ip.command("delete")
@click.argument("ip")
@click.pass_context
def fip_delete(ctx, ip):
    _api("delete", f"{DEFAULT_URLS['network']}/floating-ips/{ip}", ctx.obj["profile"])
    click.echo(f"✓ Floating IP {ip} deleted")


# ═══════════════════════════════════════════════════════════════════════════════
# SECURITY GROUP
# ═══════════════════════════════════════════════════════════════════════════════
@cli.group("security-group")
@click.pass_context
def security_group(ctx):
    """Manage security groups"""


@security_group.command("create")
@click.argument("name")
@click.option("--description", "-d", default="")
@click.pass_context
def sg_create(ctx, name, description):
    data = _api("post", f"{DEFAULT_URLS['network']}/security-groups", ctx.obj["profile"],
                json={"name": name, "description": description})
    _output(data, ctx.obj["fmt"])


@security_group.command("list")
@click.pass_context
def sg_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['network']}/security-groups", ctx.obj["profile"])
    _output(data.get("items", []), ctx.obj["fmt"], ["id", "name", "description"])


@security_group.command("show")
@click.argument("ref")
@click.pass_context
def sg_show(ctx, ref):
    data = _api("get", f"{DEFAULT_URLS['network']}/security-groups/{ref}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])


@security_group.command("delete")
@click.argument("ref")
@click.pass_context
def sg_delete(ctx, ref):
    _api("delete", f"{DEFAULT_URLS['network']}/security-groups/{ref}", ctx.obj["profile"])
    click.echo(f"✓ Security group {ref} deleted")


@security_group.group("rule")
def sg_rule():
    """Manage security group rules"""


@sg_rule.command("add")
@click.argument("sg_ref")
@click.option("--direction", type=click.Choice(["ingress", "egress"]), default="ingress")
@click.option("--protocol", type=click.Choice(["tcp", "udp", "icmp", "any"]), default="tcp")
@click.option("--port", type=int)
@click.option("--port-range-min", type=int)
@click.option("--port-range-max", type=int)
@click.option("--ethertype", type=click.Choice(["IPv4", "IPv6"]), default=None, help="Default: IPv6 if --remote-ip is an IPv6 prefix, else IPv4")
@click.option("--remote-ip", default=None, help="Remote prefix. Default: 0.0.0.0/0, or ::/0 with --ethertype IPv6")
@click.pass_context
def sg_rule_add(ctx, sg_ref, direction, protocol, port, port_range_min, port_range_max, ethertype, remote_ip):
    """Add a rule. The default security group only carries IPv4 rules: IPv6 traffic needs rules with --ethertype IPv6."""
    ethertype = ethertype or ("IPv6" if remote_ip and ":" in remote_ip else "IPv4")
    remote_ip = remote_ip or ("::/0" if ethertype == "IPv6" else "0.0.0.0/0")
    payload = {"direction": direction, "protocol": protocol, "ethertype": ethertype, "remote_ip_prefix": remote_ip}
    if port:             payload["port_min"] = payload["port_max"] = port
    if port_range_min:   payload["port_min"] = port_range_min
    if port_range_max:   payload["port_max"] = port_range_max
    data = _api("post", f"{DEFAULT_URLS['network']}/security-groups/{sg_ref}/rules",
                ctx.obj["profile"], json=payload)
    _output(data, ctx.obj["fmt"])


@sg_rule.command("list")
@click.argument("sg_ref")
@click.pass_context
def sg_rule_list(ctx, sg_ref):
    data = _api("get", f"{DEFAULT_URLS['network']}/security-groups/{sg_ref}/rules", ctx.obj["profile"])
    _output(data.get("items", data), ctx.obj["fmt"],
            ["id", "direction", "protocol", "port_min", "port_max", "remote_ip_prefix"])


@sg_rule.command("delete")
@click.argument("rule_id")
@click.pass_context
def sg_rule_delete(ctx, rule_id):
    _api("delete", f"{DEFAULT_URLS['network']}/security-group-rules/{rule_id}", ctx.obj["profile"])
    click.echo(f"✓ Rule {rule_id} deleted")


# ═══════════════════════════════════════════════════════════════════════════════
# VPC PEERING & BGP (Networking extensions)
# ═══════════════════════════════════════════════════════════════════════════════
@cli.group("vpc-peering")
@click.pass_context
def vpc_peering(ctx):
    """VPC peering between routers"""


@vpc_peering.command("list")
@click.pass_context
def vpc_peering_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['network']}/vpc-peerings", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"], ["id", "name", "requester_router_name", "accepter_router_name", "status"])


@vpc_peering.command("create")
@click.argument("name")
@click.option("--requester", required=True)
@click.option("--accepter", required=True)
@click.pass_context
def vpc_peering_create(ctx, name, requester, accepter):
    payload = {"name": name, "accepter_router_id": accepter}
    data = _api("post", f"{DEFAULT_URLS['network']}/routers/{requester}/peering", ctx.obj["profile"], json=payload)
    _output(data, ctx.obj["fmt"])


@vpc_peering.command("show")
@click.argument("peering_id")
@click.pass_context
def vpc_peering_show(ctx, peering_id):
    data = _api("get", f"{DEFAULT_URLS['network']}/vpc-peerings/{peering_id}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])


@vpc_peering.command("delete")
@click.argument("peering_id")
@click.pass_context
def vpc_peering_delete(ctx, peering_id):
    _api("delete", f"{DEFAULT_URLS['network']}/vpc-peerings/{peering_id}", ctx.obj["profile"])
    click.echo(f"✓ VPC peering {peering_id} deleted")


@cli.group("bgp-peer")
@click.pass_context
def bgp_peer(ctx):
    """BGP peer management for VPC routing"""


@bgp_peer.command("list")
@click.pass_context
def bgp_peer_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['network']}/bgp-peers", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"], ["id", "name", "router_id", "peer_ip", "local_as", "remote_as", "status", "status_reason", "outer_ip"])


@bgp_peer.command("create")
@click.argument("name")
@click.option("--router", "router_id", required=True)
@click.option("--peer-ip", required=True)
@click.option("--local-as", type=int, required=True)
@click.option("--remote-as", type=int, required=True)
@click.option("--auth-password")
@click.option("--advertised-prefix", "advertised_prefixes", multiple=True)
@click.pass_context
def bgp_peer_create(ctx, name, router_id, peer_ip, local_as, remote_as, auth_password, advertised_prefixes):
    payload = {"name": name, "router_id": router_id, "peer_ip": peer_ip, "local_as": local_as, "remote_as": remote_as}
    if auth_password: payload["auth_password"] = auth_password
    if advertised_prefixes: payload["advertised_prefixes"] = list(advertised_prefixes)
    data = _api("post", f"{DEFAULT_URLS['network']}/bgp-peers", ctx.obj["profile"], json=payload)
    _output(data, ctx.obj["fmt"])


@bgp_peer.command("show")
@click.argument("peer_id")
@click.pass_context
def bgp_peer_show(ctx, peer_id):
    data = _api("get", f"{DEFAULT_URLS['network']}/bgp-peers/{peer_id}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])


@bgp_peer.command("delete")
@click.argument("peer_id")
@click.pass_context
def bgp_peer_delete(ctx, peer_id):
    _api("delete", f"{DEFAULT_URLS['network']}/bgp-peers/{peer_id}", ctx.obj["profile"])
    click.echo(f"✓ BGP peer {peer_id} deleted")


@bgp_peer.command("sync-config")
@click.argument("peer_id")
@click.pass_context
def bgp_peer_sync_config(ctx, peer_id):
    data = _api("post", f"{DEFAULT_URLS['network']}/bgp-peers/{peer_id}/sync-config", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])


# ═══════════════════════════════════════════════════════════════════════════════
# VPNAAS (IPsec VPN)
# ═══════════════════════════════════════════════════════════════════════════════
@cli.group("vpn")
@click.pass_context
def vpn(ctx):
    """IPsec Site-to-Site VPN management"""


@vpn.group("ikepolicy")
@click.pass_context
def vpn_ikepolicy(ctx):
    """IKE Policy management"""


@vpn_ikepolicy.command("list")
@click.pass_context
def vpn_ikepolicy_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['network']}/vpn/ikepolicies", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"], ["id", "name", "auth_algorithm", "encryption_algorithm", "phase1_negotiation_mode", "pfs"])


@vpn_ikepolicy.command("create")
@click.argument("name")
@click.option("--auth-algo", default="sha256")
@click.option("--enc-algo", default="aes-256")
@click.option("--pfs", default="group14")
@click.pass_context
def vpn_ikepolicy_create(ctx, name, auth_algo, enc_algo, pfs):
    body = {"name": name, "auth_algorithm": auth_algo, "encryption_algorithm": enc_algo, "pfs": pfs}
    data = _api("post", f"{DEFAULT_URLS['network']}/vpn/ikepolicies", ctx.obj["profile"], json=body)
    _output(data, ctx.obj["fmt"])


@vpn_ikepolicy.command("delete")
@click.argument("policy_id")
@click.pass_context
def vpn_ikepolicy_delete(ctx, policy_id):
    _api("delete", f"{DEFAULT_URLS['network']}/vpn/ikepolicies/{policy_id}", ctx.obj["profile"])
    click.echo(f"✓ IKE Policy {policy_id} deleted")


@vpn.group("ipsecpolicy")
@click.pass_context
def vpn_ipsecpolicy(ctx):
    """IPsec Policy management"""


@vpn_ipsecpolicy.command("list")
@click.pass_context
def vpn_ipsecpolicy_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['network']}/vpn/ipsecpolicies", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"], ["id", "name", "auth_algorithm", "encryption_algorithm", "pfs", "transform_protocol"])


@vpn_ipsecpolicy.command("create")
@click.argument("name")
@click.option("--auth-algo", default="sha256")
@click.option("--enc-algo", default="aes-256")
@click.option("--pfs", default="group14")
@click.pass_context
def vpn_ipsecpolicy_create(ctx, name, auth_algo, enc_algo, pfs):
    body = {"name": name, "auth_algorithm": auth_algo, "encryption_algorithm": enc_algo, "pfs": pfs}
    data = _api("post", f"{DEFAULT_URLS['network']}/vpn/ipsecpolicies", ctx.obj["profile"], json=body)
    _output(data, ctx.obj["fmt"])


@vpn_ipsecpolicy.command("delete")
@click.argument("policy_id")
@click.pass_context
def vpn_ipsecpolicy_delete(ctx, policy_id):
    _api("delete", f"{DEFAULT_URLS['network']}/vpn/ipsecpolicies/{policy_id}", ctx.obj["profile"])
    click.echo(f"✓ IPsec Policy {policy_id} deleted")


@vpn.group("connection")
@click.pass_context
def vpn_connection(ctx):
    """VPN Site Connection management"""


@vpn_connection.command("list")
@click.pass_context
def vpn_connection_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['network']}/vpn/site-connections", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"], ["id", "name", "router_id", "peer_address", "peer_cidrs", "status", "status_reason"])


@vpn_connection.command("create")
@click.argument("name")
@click.option("--router", "router_id", required=True)
@click.option("--peer-address", required=True)
@click.option("--peer-id", required=True)
@click.option("--peer-cidr", "peer_cidrs", multiple=True, required=True)
@click.option("--psk", required=True)
@click.option("--ikepolicy", "ikepolicy_id", required=True)
@click.option("--ipsecpolicy", "ipsecpolicy_id", required=True)
@click.pass_context
def vpn_connection_create(ctx, name, router_id, peer_address, peer_id, peer_cidrs, psk, ikepolicy_id, ipsecpolicy_id):
    body = {
        "name": name,
        "router_id": router_id,
        "peer_address": peer_address,
        "peer_id": peer_id,
        "peer_cidrs": list(peer_cidrs),
        "psk": psk,
        "ikepolicy_id": ikepolicy_id,
        "ipsecpolicy_id": ipsecpolicy_id,
    }
    data = _api("post", f"{DEFAULT_URLS['network']}/vpn/site-connections", ctx.obj["profile"], json=body)
    _output(data, ctx.obj["fmt"])


@vpn_connection.command("show")
@click.argument("conn_id")
@click.pass_context
def vpn_connection_show(ctx, conn_id):
    data = _api("get", f"{DEFAULT_URLS['network']}/vpn/site-connections/{conn_id}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])


@vpn_connection.command("delete")
@click.argument("conn_id")
@click.pass_context
def vpn_connection_delete(ctx, conn_id):
    _api("delete", f"{DEFAULT_URLS['network']}/vpn/site-connections/{conn_id}", ctx.obj["profile"])
    click.echo(f"✓ VPN site connection {conn_id} deleted")


@vpn_connection.command("config")
@click.argument("conn_id")
@click.pass_context
def vpn_connection_config(ctx, conn_id):
    data = _api("post", f"{DEFAULT_URLS['network']}/vpn/site-connections/{conn_id}/config", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])
