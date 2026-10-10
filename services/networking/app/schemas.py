import ipaddress
import re
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class NetworkIn(BaseModel):
    name: str
    external: bool = False
    mtu: int | None = Field(default=None, ge=68, le=9216)

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

class Route(BaseModel):
    destination: str
    nexthop: str

    @field_validator("destination")
    @classmethod
    def _v_dest(cls, v):
        return str(ipaddress.ip_network(v, strict=False))

    @field_validator("nexthop")
    @classmethod
    def _v_hop(cls, v):
        return str(ipaddress.ip_address(v))

class RouterUpdate(BaseModel):
    routes: list[Route] | None = None

class AddressPair(BaseModel):
    ip_address: str
    mac_address: str | None = Field(default=None, pattern=r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")

    @field_validator("ip_address")
    @classmethod
    def _v_ip(cls, v):
        return str(ipaddress.ip_interface(v).network if "/" in v else ipaddress.ip_address(v))

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
    allowed_address_pairs: list[AddressPair] = Field(default_factory=list)

class PortUpdate(BaseModel):
    name: str | None = None
    allowed_address_pairs: list[AddressPair] | None = None
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

def _ip(v: str) -> str:
    return str(ipaddress.ip_address(v))

def _host(v: str) -> str:
    try:
        return _ip(v)
    except ValueError:
        if not re.fullmatch(r"(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9-]{0,62}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,62}[A-Za-z0-9])?)*", v):
            raise ValueError("invalid address")
        return v

def _nets(v):
    return v and [str(ipaddress.ip_network(c, strict=False)) for c in v]

class BGPPeerIn(BaseModel):
    # values end up in an FRR config snippet: keep them to a safe alphabet
    name: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    router_id: str
    peer_ip: str
    remote_as: int = Field(ge=1, le=4294967295)
    local_as: int = Field(ge=1, le=4294967295)
    auth_password: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.:!@%^*+=-]{1,80}$")
    advertised_prefixes: list[str] | None = None

    @field_validator("peer_ip")
    @classmethod
    def _v_ip(cls, v):
        return _ip(v)

    @field_validator("advertised_prefixes")
    @classmethod
    def _v_pfx(cls, v):
        return _nets(v)

class BGPPeerOut(BaseModel):
    id: str
    project_id: str
    name: str
    router_id: str
    peer_ip: str
    remote_as: int
    local_as: int
    has_password: bool = False
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
    # values are interpolated into strongSwan ipsec.conf/ipsec.secrets: keep them to a safe alphabet
    name: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    router_id: str
    peer_address: str
    peer_id: str = Field(pattern=r"^[A-Za-z0-9@._:-]{1,128}$")
    peer_cidrs: list[str]
    psk: str = Field(min_length=8, max_length=128, pattern=r"^[^\s\"'#{}\\]+$")
    ikepolicy_id: str
    ipsecpolicy_id: str
    admin_state_up: bool = True

    @field_validator("peer_address")
    @classmethod
    def _v_addr(cls, v):
        return _host(v)

    @field_validator("peer_cidrs")
    @classmethod
    def _v_cidrs(cls, v):
        return _nets(v)

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
