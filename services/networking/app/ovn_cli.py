import ipaddress
import json
import os
import socket

from fastapi import HTTPException
from shared.proc import run
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import INTERNAL_VIP_ADDRESS, OVN_NB_DB, PROVIDER_BRIDGE, PROVIDER_PHYSNET
from .ipam import parse_id_list
from .models import Network, Port, Router, SecurityGroup, SecurityGroupRule, stable_mac

# ovn-nbctl/ovs-vsctl get --timeout=8 themselves; this is the hard stop if the process hangs anyway.
OVN_TIMEOUT = 20


def ovn(*args: str, check: bool = True) -> str:
    cmd = ["ovn-nbctl", f"--db={OVN_NB_DB}", "--timeout=8", *args]
    proc = run(cmd, check=False, timeout=OVN_TIMEOUT)
    if check and proc.returncode != 0:
        raise HTTPException(status_code=502, detail={"message": "ovn-nbctl failed", "stderr": proc.stderr.strip()})
    return proc.stdout.strip()

def _ovn_register_dns(logical_switch_name: str, hostname: str, ip_address: str) -> None:
    """
    Register a DNS record in the OVN Northbound DB for a port.

    This creates (or updates) a DNS entry in the OVN NB `DNS` table and
    associates it with the logical switch, enabling internal VM hostname
    resolution. VMs on the same network can resolve each other by hostname.

    Foundation for future Designate integration: when a Designate-like service
    is built, it can call GET /ports?has_dns=true and sync records to an external
    DNS backend (Bind9/PowerDNS) without touching this OVN internal DNS layer.
    """
    try:
        # Check if a DNS entry already exists for this switch
        existing = ovn("find", "DNS", f"external_ids:ls={logical_switch_name}", check=False)
        if existing:
            # Update existing DNS entry — add this hostname to the records
            dns_uuid = existing.split()[0]
            ovn("set", "DNS", dns_uuid,
                f'records:{hostname}="{ip_address}"',
                check=False)
        else:
            # Create a new DNS entry for this logical switch
            dns_uuid = ovn("create", "DNS",
                           f'records:"{hostname}"="{ip_address}"',
                           f"external_ids:ls={logical_switch_name}",
                           check=False)
            if dns_uuid:
                # Associate the DNS entry with the logical switch
                ovn("add", "Logical_Switch", logical_switch_name, "dns_records", dns_uuid, check=False)
    except Exception as exc:
        # DNS registration failure is non-fatal; log and continue
        print(f"[networking] WARNING: OVN DNS registration failed for {hostname}: {exc}", flush=True)

def _ovn_unregister_dns(logical_switch_name: str, hostname: str) -> None:
    """Remove a DNS hostname record from the OVN NB DNS table."""
    try:
        existing = ovn("find", "DNS", f"external_ids:ls={logical_switch_name}", check=False)
        if existing:
            dns_uuid = existing.split()[0]
            ovn("remove", "DNS", dns_uuid, "records", hostname, check=False)
    except Exception as exc:
        print(f"[networking] WARNING: OVN DNS removal failed for {hostname}: {exc}", flush=True)

def _find_ovs_vsctl() -> str | None:
    """Find ovs-vsctl binary in common installation paths."""
    import shutil
    # Try PATH first, then common OVS installation directories
    for candidate in ["ovs-vsctl", "/usr/bin/ovs-vsctl", "/usr/local/bin/ovs-vsctl",
                      "/sbin/ovs-vsctl", "/usr/sbin/ovs-vsctl"]:
        found = shutil.which(candidate)
        if found:
            return found
    return None

_OVS_VSCTL: str | None = _find_ovs_vsctl()

if _OVS_VSCTL is None:
    print("[networking-api] WARNING: ovs-vsctl not found — OVS bridge-mapping updates will be skipped.", flush=True)

def ovs(*args: str, check: bool = True) -> str:
    if _OVS_VSCTL is None:
        if check:
            raise HTTPException(status_code=503, detail={"message": "ovs-vsctl not available in this environment"})
        return ""
    cmd = [_OVS_VSCTL, "--timeout=8", *args]
    proc = run(cmd, check=False, timeout=OVN_TIMEOUT)
    if check and proc.returncode != 0:
        raise HTTPException(status_code=502, detail={"message": "ovs-vsctl failed", "stderr": proc.stderr.strip()})
    return proc.stdout.strip()

def sbctl(*args: str) -> str:
    cmd = ["ovn-sbctl", f"--db={os.environ.get('OVN_SB_DB', 'tcp:127.0.0.1:6642')}", "--timeout=8", *args]
    return run(cmd, check=False, timeout=OVN_TIMEOUT).stdout.strip()

def _chassis_table() -> list[dict]:
    """Southbound Chassis rows as {name, hostname, config}; empty when ovn-sbctl cannot answer."""
    try:
        data = json.loads(sbctl("--format=json", "--columns=name,hostname,other_config", "find", "Chassis"))["data"]
    except (ValueError, KeyError):
        return []
    return [{"name": name, "hostname": hostname, "config": dict(config[1]) if config and config[0] == "map" else {}}
            for name, hostname, config in data]

def _can_be_gateway(chassis: dict) -> bool:
    """enable-chassis-as-gw and a bridge mapping for the provider network: the chassis has an uplink to carry NAT traffic."""
    config = chassis["config"]
    mapped = [m.split(":", 1)[0] for m in config.get("ovn-bridge-mappings", "").split(",") if m]
    return "enable-chassis-as-gw" in config.get("ovn-cms-options", "").split(",") and PROVIDER_PHYSNET in mapped

def get_local_chassis_id() -> str | None:
    """Chassis to pin the router gateway to: this host's own if it can be a gateway, else the first gateway-capable one.

    The local chassis is matched by host name in the Southbound DB. Asking Open vSwitch for the system-id does not work
    here: this service runs as the unprivileged `cloudx` user, and the id changes when Open vSwitch restarts.
    Without any gateway-capable chassis (single-node labs that never set enable-chassis-as-gw) the local chassis, or
    the first one, is used as before.
    """
    chassis = _chassis_table()
    short = socket.gethostname().split(".")[0]
    mine = [c for c in chassis if c["hostname"].split(".")[0] == short]
    gateways = sorted((c for c in chassis if _can_be_gateway(c)), key=lambda c: c["hostname"])
    preference = [*(c for c in mine if _can_be_gateway(c)), *gateways, *mine, *chassis[:1]]
    return preference[0]["name"] if preference else None
def localnet_port_name(network_name: str) -> str:
    return f"{network_name}-localnet"

def localnet_commands(network_name: str, physnet: str) -> list[list[str]]:
    port_name = localnet_port_name(network_name)
    return [
        ["--may-exist", "lsp-add", network_name, port_name],
        ["lsp-set-type", port_name, "localnet"],
        ["lsp-set-addresses", port_name, "unknown"],
        ["lsp-set-options", port_name, f"network_name={physnet}"],
        ["set", "logical_switch_port", port_name, "external_ids:cloudx=true", "external_ids:provider-localnet=true"],
    ]

def ensure_ovn_bridge_mapping(physnet: str, bridge: str) -> None:
    """Add physnet:bridge mapping to OVS external_ids. Non-fatal if OVS unavailable."""
    if _OVS_VSCTL is None:
        print(f"[networking-api] WARNING: skipping bridge-mapping update ({physnet}:{bridge}) — ovs-vsctl not found", flush=True)
        return
    existing = ovs("get", "open", ".", "external_ids:ovn-bridge-mappings", check=False).strip().strip('"')
    mapping = f"{physnet}:{bridge}"
    if existing and existing != "[]":
        mappings = [item for item in existing.split(",") if item]
        if mapping in mappings:
            return
        value = ",".join([*mappings, mapping])
    else:
        value = mapping
    ovs("set", "open", ".", f"external_ids:ovn-bridge-mappings={value}", check=False)

def external_network_defaults(name: str) -> dict:
    cidr = "172.24.4.0/24"
    gateway = "172.24.4.1"
    if "/" in INTERNAL_VIP_ADDRESS:
        import ipaddress
        try:
            net = ipaddress.ip_network(INTERNAL_VIP_ADDRESS, strict=False)
            cidr = str(net)
            gateway = str(net.network_address + 1)
        except ValueError:
            pass
    # Explicit env overrides (see docs/provider-network.md). Required when the
    # provider network is bridged onto a physical LAN: network_address+1 may
    # collide with the real LAN gateway, so operators must be able to pick a
    # free IP for the OVN router's external port.
    cidr = os.getenv("PROVIDER_CIDR") or cidr
    gateway = os.getenv("PROVIDER_GATEWAY") or gateway
    return {
        "provider": True,
        "physical_network": PROVIDER_PHYSNET,
        "bridge_mapping": f"{PROVIDER_PHYSNET}:{PROVIDER_BRIDGE}",
        "provider_bridge": PROVIDER_BRIDGE,
        "provider_cidr": cidr,
        "gateway_ip": gateway,
        "allocation_start": os.getenv("PROVIDER_ALLOCATION_START") or "172.24.4.100",
        "allocation_end": os.getenv("PROVIDER_ALLOCATION_END") or "172.24.4.200",
    }

def ensure_provider_ovn(network_name: str, physnet: str, bridge: str) -> None:
    ovn("--may-exist", "ls-add", network_name)
    ovn(
        "set",
        "logical_switch",
        network_name,
        "external_ids:cloudx=true",
        "external_ids:network_type=external",
        f"external_ids:physical_network={physnet}",
        f"external_ids:bridge_mapping={physnet}:{bridge}",
    )
    for command in localnet_commands(network_name, physnet):
        ovn(*command)
    ensure_ovn_bridge_mapping(physnet, bridge)

def dhcp_options_args(cidr: str, gateway: str, dns_nameservers: list[str], server_mac: str = "02:00:00:00:00:01", mtu: int | None = None) -> list[str]:
    args = [
        "create",
        "DHCP_Options",
        f"cidr={cidr}",
        "external_ids:cloudx=true",
        f"options:server_id={gateway}",
        f"options:router={gateway}",
        f"options:server_mac={server_mac}",
        "options:lease_time=3600",
    ]
    if mtu:
        args.append(f"options:mtu={mtu}")
    if dns_nameservers:
        args.append(f"options:dns_server={{{','.join(dns_nameservers)}}}")
    return args

def _ensure_dhcpv6_options(
    cidr: str,
    gateway: str | None,
    ipv6_ra_mode: str | None,
    ipv6_address_mode: str | None,
) -> str | None:
    """
    Set up OVN DHCPv6 options for an IPv6 subnet.

    Mode mapping:
      dhcpv6-stateful  → OVN DHCPv6 options with dhcpv6_stateful_opt
      dhcpv6-stateless → OVN DHCPv6 options without address (SLAAC for addr, DHCPv6 for opts)
      slaac            → Router Advertisement only — no OVN DHCP_Options entry needed

    Returns the OVN DHCP_Options UUID or None.
    """
    if ipv6_ra_mode == "slaac" and ipv6_address_mode == "slaac":
        # Pure SLAAC — no DHCP_Options in OVN, RA handles everything
        return None

    if not gateway:
        # No gateway → cannot set up server_id for DHCPv6
        return None

    try:
        dhcp_uuid = ovn(
            "create",
            "DHCP_Options",
            f"cidr={cidr}",
            "external_ids:cloudx=true",
            f"external_ids:ipv6=true",
            f"options:server_id={gateway}",
        )
        return dhcp_uuid
    except Exception as exc:
        import logging
        logging.getLogger("networking-api").warning(
            "DHCPv6 options setup failed for %s: %s", cidr, exc
        )
        return None

def router_external_port_names(router_name: str) -> tuple[str, str]:
    return f"lrp-{router_name}-external", f"lsp-{router_name}-external"

def ensure_router_gateway_ovn(router: Router, network: Network, router_ip: str | None = None) -> None:
    """gateway_ip is the upstream next-hop; router_ip is the router's own address on the provider net."""
    if not network.gateway_ip or not network.provider_cidr:
        raise HTTPException(status_code=400, detail="external network is missing provider gateway/cidr")
    cidr = ipaddress.ip_network(network.provider_cidr, strict=False)
    lrp, lsp = router_external_port_names(router.name)
    port_mac = stable_mac(f"{router.id}-{network.id}-external")
    ovn("--may-exist", "lrp-add", router.name, lrp, port_mac, f"{router_ip or network.gateway_ip}/{cidr.prefixlen}")
    if router_ip and router_ip != network.gateway_ip:
        ovn("set", "logical_router_port", lrp, f'networks="{router_ip}/{cidr.prefixlen}"')
        ovn("--may-exist", "lr-route-add", router.name, "0.0.0.0/0", network.gateway_ip)
    ovn("set", "logical_router_port", lrp, "external_ids:cloudx=true", "external_ids:external-gateway=true")
    ovn("--may-exist", "lsp-add", network.name, lsp)
    ovn("set", "logical_switch_port", lsp, "external_ids:cloudx=true", "external_ids:external-gateway=true")
    ovn("lsp-set-type", lsp, "router")
    ovn("lsp-set-addresses", lsp, "router")
    ovn("lsp-set-options", lsp, f"router-port={lrp}")
    # Auto-set gateway chassis so proxy ARP and NAT flows are installed automatically
    chassis_id = get_local_chassis_id()
    if chassis_id:
        ovn("lrp-set-gateway-chassis", lrp, chassis_id, check=False)
        print(f"gateway chassis set: {lrp} -> {chassis_id}", flush=True)
    else:
        print("WARNING: could not find local chassis ID from OVN SB; run lrp-set-gateway-chassis manually", flush=True)

def nat_rule_args(router_name: str, external_ip: str, logical_ip: str) -> list[str]:
    return ["--may-exist", "lr-nat-add", router_name, "dnat_and_snat", external_ip, logical_ip]

def port_group_name(security_group_id: str) -> str:
    return f"pg_{security_group_id.replace('-', '')}"

def acl_match(security_group_id: str, rule: SecurityGroupRule) -> str:
    port_field = "outport" if rule.direction == "ingress" else "inport"
    v6 = rule.ethertype == "IPv6"
    ip = "ip6" if v6 else "ip4"
    remote_field = f"{ip}.src" if rule.direction == "ingress" else f"{ip}.dst"
    parts = [f"{port_field} == @{port_group_name(security_group_id)}", ip]
    protocol = rule.protocol or "any"
    if protocol == "icmp":
        parts.append("icmp6" if v6 else "icmp4")
    elif protocol != "any":
        parts.append(protocol)
    if protocol in {"tcp", "udp"} and rule.port_min:
        dst_field = f"{protocol}.dst"
        if rule.port_max and rule.port_max != rule.port_min:
            parts.append(f"{rule.port_min} <= {dst_field} && {dst_field} <= {rule.port_max}")
        else:
            parts.append(f"{dst_field} == {rule.port_min}")
    if rule.remote_ip_prefix:
        parts.append(f"{remote_field} == {rule.remote_ip_prefix}")
    if rule.remote_group_id:
        remote_port_field = "inport" if rule.direction == "ingress" else "outport"
        parts.append(f"{remote_port_field} == @{port_group_name(rule.remote_group_id)}")
    return " && ".join(parts)

def acl_priority(rule: SecurityGroupRule) -> str:
    if rule.protocol in {"tcp", "udp"} and rule.port_min:
        return "2002"
    return "2001"

def ensure_security_group_port_group(group: SecurityGroup) -> str:
    pg = port_group_name(group.id)
    # pg-add does not support --may-exist; check existence first
    existing = ovn("--format=list", "--columns=name", "find", "port_group", f"name={pg}", check=False)
    if not existing:
        ovn("pg-add", pg)
    ovn("set", "port_group", pg, "external_ids:cloudx=true", f"external_ids:security_group_id={group.id}", f"external_ids:name={group.name}", check=False)
    return pg

def apply_security_group_acls(db: Session, group: SecurityGroup) -> None:
    pg = ensure_security_group_port_group(group)
    ovn("--type=port-group", "acl-del", pg)  # no --if-exists: acl-del does not take it; pg exists, ensured above
    rules = db.scalars(select(SecurityGroupRule).where(SecurityGroupRule.security_group_id == group.id)).all()
    for rule in rules:
        direction = "to-lport" if rule.direction == "ingress" else "from-lport"
        action = "allow-related" if rule.action == "allow" else rule.action
        ovn("--type=port-group", "--may-exist", "acl-add", pg, direction, acl_priority(rule), acl_match(group.id, rule), action)
    ovn("--type=port-group", "--may-exist", "acl-add", pg, "to-lport", "1001", f"outport == @{pg} && ip4", "drop")
    # no `!nd`: ovn-controller refuses to compile negated nominal predicates, so the flow was never installed.
    # OVN's built-in priority 65532 flows already allow ND/MLD, so a plain ip6 drop is safe.
    ovn("--type=port-group", "--may-exist", "acl-add", pg, "to-lport", "1001", f"outport == @{pg} && ip6", "drop")

def get_lsp_uuid(logical_port_name: str) -> str | None:
    """Resolve a Logical_Switch_Port name to its OVN NB UUID.

    pg-add-port / pg-del-port require the UUID of the LSP object, not its
    name string.  Without this look-up the commands fail silently (check=False)
    and the port is never added to the port group, so ACLs never apply.
    """
    out = ovn(
        "--format=list", "--columns=_uuid",
        "find", "Logical_Switch_Port", f"name={logical_port_name}",
        check=False,
    )
    # output looks like:  _uuid               : <uuid>
    for line in out.splitlines():
        if line.startswith("_uuid"):
            return line.split(":", 1)[-1].strip()
    return None

def refresh_port_group_membership(db: Session, port: Port) -> None:
    if not port.logical_port_name:
        return
    # pg-add-port / pg-del-port are not available in older OVN versions.
    # Use the generic OVSDB mutation commands instead:
    #   ovn-nbctl add    port_group <pg> ports <lsp-uuid>   (idempotent set-add)
    #   ovn-nbctl remove port_group <pg> ports <lsp-uuid>   (idempotent set-remove)
    # These require the LSP UUID, not its name.
    lsp_uuid = get_lsp_uuid(port.logical_port_name)
    groups = db.scalars(select(SecurityGroup)).all()
    selected = set(parse_id_list(port.security_group_ids)) if port.port_security_enabled else set()
    for group in groups:
        pg = ensure_security_group_port_group(group)
        if group.id in selected:
            if lsp_uuid:
                ovn("add", "port_group", pg, "ports", lsp_uuid, check=False)
            else:
                print(f"[warn] refresh_port_group_membership: LSP UUID not found for {port.logical_port_name!r}, skipping", flush=True)
        else:
            if lsp_uuid:
                ovn("remove", "port_group", pg, "ports", lsp_uuid, check=False)

def apply_security_groups(db: Session, port: Port) -> None:
    refresh_port_group_membership(db, port)
    for group in db.scalars(select(SecurityGroup)).all():
        apply_security_group_acls(db, group)
