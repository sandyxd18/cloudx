from datetime import datetime

from pydantic import BaseModel, Field


class NetworkIn(BaseModel):
    name: str
    external: bool = False

class SubnetIn(BaseModel):
    name: str
    network: str
    cidr: str
    gateway: str | None = None
    gateway_ip: str | None = None
    allocation_start: str | None = None
    allocation_end: str | None = None
    dns_nameservers: list[str] = Field(default_factory=list)
    enable_dhcp: bool = True
    ip_version: int = 4                # 4 | 6
    ipv6_ra_mode: str | None = None    # slaac | dhcpv6-stateful | dhcpv6-stateless
    ipv6_address_mode: str | None = None

class RouterIn(BaseModel):
    name: str

class RouterInterfaceIn(BaseModel):
    subnet: str

class PortIn(BaseModel):
    name: str
    network: str
    ip_address: str | None = None
    fixed_ip: str | None = None
    fixed_ips: list[dict] = Field(default_factory=list)  # [{"subnet_id":..., "ip_address":...}] dual-stack
    device_id: str | None = None
    device_owner: str | None = None
    security_group_ids: list[str] = Field(default_factory=list)
    port_security_enabled: bool = True
    dns_name: str | None = None    # optional DNS hostname registered in OVN
    qos_max_kbps: int | None = None
    qos_burst_kbps: int | None = None

class PortUpdate(BaseModel):
    name: str | None = None
    qos_max_kbps: int | None = None
    qos_burst_kbps: int | None = None

class TrunkIn(BaseModel):
    name: str
    description: str | None = None
    port_id: str                               # parent port UUID
    project_id: str | None = None

class TrunkSubPortIn(BaseModel):
    port_id: str
    segmentation_type: str = "vlan"            # vlan | vxlan
    segmentation_id: int                       # VLAN tag 1-4094

class TrunkSubPortsPayload(BaseModel):
    sub_ports: list[TrunkSubPortIn]

class FloatingIPIn(BaseModel):
    name: str | None = None
    network: str | None = None
    external_network: str | None = None
    floating_ip: str | None = Field(default=None, examples=["203.0.113.10"])
    fixed_port: str | None = None

class FloatingIPAssociateIn(BaseModel):
    port: str | None = None
    server: str | None = None
    router: str | None = None

class RouterGatewayIn(BaseModel):
    external_network: str | None = None
    # backward-compat: some callers send 'network' instead of 'external_network'
    network: str | None = None

    @property
    def resolved_network(self) -> str | None:
        return self.external_network or self.network

class SecurityGroupIn(BaseModel):
    name: str
    description: str | None = None
    project_id: str | None = None

class SecurityGroupRuleIn(BaseModel):
    direction: str
    ethertype: str = "IPv4"
    protocol: str = "any"
    port_min: int | None = None
    port_max: int | None = None
    remote_ip_prefix: str | None = None
    remote_group_id: str | None = None
    action: str = "allow"

class PortSecurityGroupsIn(BaseModel):
    security_group_ids: list[str] = Field(default_factory=list)

# ═══════════════════════════════════════════════════════════════════════════════
# VPC Peering (Phase 6.3)
# ═══════════════════════════════════════════════════════════════════════════════

class VPCPeeringIn(BaseModel):
    name: str
    accepter_router_id: str

class VPCPeeringOut(BaseModel):
    id: str
    name: str
    project_id: str
    requester_router_id: str
    accepter_router_id: str
    requester_router_name: str
    accepter_router_name: str
    status: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

# ═══════════════════════════════════════════════════════════════════════════════
# BGP Dynamic Gateway (Phase 6.3)
# ═══════════════════════════════════════════════════════════════════════════════

class BGPPeerIn(BaseModel):
    name: str
    router_id: str
    peer_ip: str
    remote_as: int
    local_as: int
    auth_password: str | None = None
    advertised_prefixes: list[str] | None = None

class BGPPeerOut(BaseModel):
    id: str
    project_id: str
    name: str
    router_id: str
    peer_ip: str
    remote_as: int
    local_as: int
    advertised_prefixes: list[str] | None
    is_active: bool
    status: str
    created_at: datetime

    class Config:
        from_attributes = True

# ═══════════════════════════════════════════════════════════════════════════════
# VPNAAS — IPSEC SITE-TO-SITE VPN (Phase 7.2)
# ═══════════════════════════════════════════════════════════════════════════════

class IKEPolicyIn(BaseModel):
    name: str
    auth_algorithm: str = "sha256"
    encryption_algorithm: str = "aes-256"
    phase1_negotiation_mode: str = "main"
    pfs: str = "group14"

class IKEPolicyOut(BaseModel):
    id: str
    name: str
    project_id: str
    auth_algorithm: str
    encryption_algorithm: str
    phase1_negotiation_mode: str
    pfs: str
    created_at: datetime

class IPsecPolicyIn(BaseModel):
    name: str
    auth_algorithm: str = "sha256"
    encryption_algorithm: str = "aes-256"
    pfs: str = "group14"
    transform_protocol: str = "esp"

class IPsecPolicyOut(BaseModel):
    id: str
    name: str
    project_id: str
    auth_algorithm: str
    encryption_algorithm: str
    pfs: str
    transform_protocol: str
    created_at: datetime

class VPNSiteConnectionIn(BaseModel):
    name: str
    router_id: str
    peer_address: str
    peer_id: str
    peer_cidrs: list[str]
    psk: str
    ikepolicy_id: str
    ipsecpolicy_id: str
    admin_state_up: bool = True

class VPNSiteConnectionOut(BaseModel):
    id: str
    name: str
    project_id: str
    router_id: str
    peer_address: str
    peer_id: str
    peer_cidrs: list[str]
    ikepolicy_id: str
    ipsecpolicy_id: str
    status: str
    admin_state_up: bool
    created_at: datetime

# ── QoS Policies (Networking QoS Extension) ────────────────────────────────────────────────────────

class QosPolicyIn(BaseModel):
    name: str
    description: str | None = None
    shared: bool = False

class QosBwLimitRuleIn(BaseModel):
    max_kbps: int
    max_burst_kbps: int = 0
    direction: str = "egress"  # egress | ingress

# ── Subnet Pools ────────────────────────────────────────────────────────────────────────────

class SubnetPoolIn(BaseModel):
    name: str
    prefixes: list[str]         # e.g. ["10.0.0.0/8", "192.168.0.0/16"]
    default_prefixlen: int = 24
    min_prefixlen: int = 8
    max_prefixlen: int = 32
    shared: bool = False

# ── Port Forwarding (DNAT per-port) ─────────────────────────────────────────────────────────


class PortForwardingIn(BaseModel):
    protocol: str = "tcp"                # tcp | udp
    external_port: int
    internal_port: int
    internal_ip_address: str
    description: str | None = None
