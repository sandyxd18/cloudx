import secrets
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass

def new_id() -> str:
    return str(uuid.uuid4())

def now() -> datetime:
    return datetime.utcnow()

def mac() -> str:
    return "02:%02x:%02x:%02x:%02x:%02x" % tuple(secrets.randbelow(256) for _ in range(5))

def stable_mac(seed: str) -> str:
    data = uuid.uuid5(uuid.NAMESPACE_DNS, seed).bytes
    return "02:%02x:%02x:%02x:%02x:%02x" % tuple(data[:5])

class Network(Base):
    __tablename__ = "networking_networks"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String, unique=True, index=True)
    external: Mapped[bool] = mapped_column(Boolean, default=False)
    shared: Mapped[bool] = mapped_column(Boolean, default=False)
    provider: Mapped[bool] = mapped_column(Boolean, default=False)
    network_type: Mapped[str | None] = mapped_column(String, nullable=True)
    physical_network: Mapped[str | None] = mapped_column(String, nullable=True)
    bridge_mapping: Mapped[str | None] = mapped_column(String, nullable=True)
    provider_bridge: Mapped[str | None] = mapped_column(String, nullable=True)
    provider_cidr: Mapped[str | None] = mapped_column(String, nullable=True)
    gateway_ip: Mapped[str | None] = mapped_column(String, nullable=True)
    allocation_start: Mapped[str | None] = mapped_column(String, nullable=True)
    allocation_end: Mapped[str | None] = mapped_column(String, nullable=True)
    project_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

class Subnet(Base):
    __tablename__ = "networking_subnets"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String, unique=True, index=True)
    network_id: Mapped[str] = mapped_column(String, ForeignKey("networking_networks.id"))
    cidr: Mapped[str] = mapped_column(String)
    gateway: Mapped[str] = mapped_column(String)
    gateway_ip: Mapped[str | None] = mapped_column(String, nullable=True)
    allocation_start: Mapped[str | None] = mapped_column(String, nullable=True)
    allocation_end: Mapped[str | None] = mapped_column(String, nullable=True)
    dns_nameservers: Mapped[str | None] = mapped_column(String, nullable=True)
    dhcp_options_id: Mapped[str | None] = mapped_column(String, nullable=True)
    enable_dhcp: Mapped[bool] = mapped_column(Boolean, default=True)
    ovn_dhcp_options_uuid: Mapped[str | None] = mapped_column(String, nullable=True)
    ip_version: Mapped[int] = mapped_column(Integer, default=4)
    ipv6_ra_mode: Mapped[str | None] = mapped_column(String, nullable=True)      # slaac | dhcpv6-stateful | dhcpv6-stateless
    ipv6_address_mode: Mapped[str | None] = mapped_column(String, nullable=True) # slaac | dhcpv6-stateful | dhcpv6-stateless
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

class Router(Base):
    __tablename__ = "networking_routers"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String, unique=True, index=True)
    external_network_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

class RouterInterface(Base):
    __tablename__ = "networking_router_interfaces"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    router_id: Mapped[str] = mapped_column(String, ForeignKey("networking_routers.id"))
    subnet_id: Mapped[str] = mapped_column(String, ForeignKey("networking_subnets.id"))
    router_port_name: Mapped[str] = mapped_column(String, unique=True)
    switch_port_name: Mapped[str] = mapped_column(String, unique=True)
    mac_address: Mapped[str] = mapped_column(String)
    ip_address: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

class Port(Base):
    __tablename__ = "networking_ports"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String, unique=True, index=True)
    network_id: Mapped[str] = mapped_column(String, ForeignKey("networking_networks.id"))
    subnet_id: Mapped[str | None] = mapped_column(String, ForeignKey("networking_subnets.id"), nullable=True)
    mac_address: Mapped[str] = mapped_column(String)
    ip_address: Mapped[str | None] = mapped_column(String, nullable=True)    # primary (IPv4) IP
    fixed_ip: Mapped[str | None] = mapped_column(String, nullable=True)      # same as ip_address (legacy)
    ipv6_address: Mapped[str | None] = mapped_column(String, nullable=True)  # IPv6 address (dual-stack)
    dns_name: Mapped[str | None] = mapped_column(String, nullable=True)      # DNS hostname for OVN DNS
    logical_switch_name: Mapped[str | None] = mapped_column(String, nullable=True)
    logical_port_name: Mapped[str | None] = mapped_column(String, nullable=True)
    device_id: Mapped[str | None] = mapped_column(String, nullable=True)
    device_owner: Mapped[str | None] = mapped_column(String, nullable=True)
    security_group_ids: Mapped[str | None] = mapped_column(String, nullable=True)
    port_security_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    qos_max_kbps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    qos_burst_kbps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

class FloatingIP(Base):
    __tablename__ = "networking_floating_ips"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String, unique=True, index=True)
    floating_ip: Mapped[str] = mapped_column(String)
    floating_ip_address: Mapped[str | None] = mapped_column(String, nullable=True)
    fixed_ip_address: Mapped[str | None] = mapped_column(String, nullable=True)
    fixed_port_id: Mapped[str | None] = mapped_column(String, ForeignKey("networking_ports.id"), nullable=True)
    port_id: Mapped[str | None] = mapped_column(String, nullable=True)
    server_id: Mapped[str | None] = mapped_column(String, nullable=True)
    external_network_id: Mapped[str | None] = mapped_column(String, nullable=True)
    router_id: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="DOWN")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

class VPCPeering(Base):
    """VPC Peering connection between two routers (Fase 6.3)."""
    __tablename__ = "networking_vpc_peerings"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String, unique=True, index=True)
    project_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    requester_router_id: Mapped[str] = mapped_column(String, ForeignKey("networking_routers.id"), nullable=False)
    accepter_router_id: Mapped[str] = mapped_column(String, ForeignKey("networking_routers.id"), nullable=False)
    requester_router_name: Mapped[str] = mapped_column(String, nullable=False)
    accepter_router_name: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, default="pending")  # pending, active, rejected, failed
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)

class BGPPeer(Base):
    """BGP peer configuration for dynamic gateway (Fase 6.3)."""
    __tablename__ = "networking_bgp_peers"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String, unique=True, index=True)
    router_id: Mapped[str] = mapped_column(String, ForeignKey("networking_routers.id"), nullable=False)
    peer_ip: Mapped[str] = mapped_column(String, nullable=False)
    remote_as: Mapped[int] = mapped_column(Integer, nullable=False)
    local_as: Mapped[int] = mapped_column(Integer, nullable=False)
    auth_password: Mapped[str | None] = mapped_column(String, nullable=True)
    advertised_prefixes: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String, default="down")  # down, established
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)

class IKEPolicy(Base):
    """VPNaaS IKE Policy (Fase 7.2)."""
    __tablename__ = "networking_ike_policies"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: f"ike-{uuid.uuid4().hex[:12]}")
    name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    project_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    auth_algorithm: Mapped[str] = mapped_column(String(32), default="sha256")
    encryption_algorithm: Mapped[str] = mapped_column(String(32), default="aes-256")
    phase1_negotiation_mode: Mapped[str] = mapped_column(String(32), default="main")
    pfs: Mapped[str] = mapped_column(String(32), default="group14")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

class IPsecPolicy(Base):
    """VPNaaS IPsec Policy (Fase 7.2)."""
    __tablename__ = "networking_ipsec_policies"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: f"ipsec-{uuid.uuid4().hex[:12]}")
    name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    project_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    auth_algorithm: Mapped[str] = mapped_column(String(32), default="sha256")
    encryption_algorithm: Mapped[str] = mapped_column(String(32), default="aes-256")
    pfs: Mapped[str] = mapped_column(String(32), default="group14")
    transform_protocol: Mapped[str] = mapped_column(String(32), default="esp")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

class VPNSiteConnection(Base):
    """VPNaaS IPsec Site-to-Site Connection (Fase 7.2)."""
    __tablename__ = "networking_vpn_site_connections"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: f"vpn-{uuid.uuid4().hex[:12]}")
    name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    project_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    router_id: Mapped[str] = mapped_column(String, ForeignKey("networking_routers.id"), nullable=False)
    peer_address: Mapped[str] = mapped_column(String, nullable=False)
    peer_id: Mapped[str] = mapped_column(String, nullable=False)
    peer_cidrs_json: Mapped[str] = mapped_column(Text, default="[]")
    psk: Mapped[str] = mapped_column(String, nullable=False)
    ikepolicy_id: Mapped[str] = mapped_column(String, ForeignKey("networking_ike_policies.id"), nullable=False)
    ipsecpolicy_id: Mapped[str] = mapped_column(String, ForeignKey("networking_ipsec_policies.id"), nullable=False)
    status: Mapped[str] = mapped_column(String, default="ACTIVE")
    admin_state_up: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

class PortForwarding(Base):
    """DNAT per-port forwarding rule attached to a floating IP."""
    __tablename__ = "networking_port_forwardings"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    floatingip_id: Mapped[str] = mapped_column(String, ForeignKey("networking_floating_ips.id", ondelete="CASCADE"))
    protocol: Mapped[str] = mapped_column(String, default="tcp")           # tcp | udp
    external_port: Mapped[int] = mapped_column(Integer)
    internal_port: Mapped[int] = mapped_column(Integer)
    internal_ip_address: Mapped[str] = mapped_column(String)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

class SecurityGroup(Base):
    __tablename__ = "networking_security_groups"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String, unique=True, index=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    project_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)

class SecurityGroupRule(Base):
    __tablename__ = "networking_security_group_rules"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    security_group_id: Mapped[str] = mapped_column(String, ForeignKey("networking_security_groups.id", ondelete="CASCADE"))
    direction: Mapped[str] = mapped_column(String)
    ethertype: Mapped[str] = mapped_column(String, default="IPv4")
    protocol: Mapped[str] = mapped_column(String, default="any")
    port_min: Mapped[str | None] = mapped_column(String, nullable=True)
    port_max: Mapped[str | None] = mapped_column(String, nullable=True)
    remote_ip_prefix: Mapped[str | None] = mapped_column(String, nullable=True)
    remote_group_id: Mapped[str | None] = mapped_column(String, nullable=True)
    action: Mapped[str] = mapped_column(String, default="allow")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

class QosPolicy(Base):
    """QoS policy (Networking QoS extension)."""
    __tablename__ = "networking_qos_policies"
    id:          Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name:        Mapped[str] = mapped_column(String, unique=True, index=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    shared:      Mapped[bool] = mapped_column(Boolean, default=False)
    project_id:  Mapped[str | None] = mapped_column(String, nullable=True)
    created_at:  Mapped[datetime] = mapped_column(DateTime, default=now)

class QosBandwidthLimitRule(Base):
    """Bandwidth-limit rule attached to a QoS policy."""
    __tablename__ = "networking_qos_bw_limit_rules"
    id:                Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    qos_policy_id:     Mapped[str] = mapped_column(String, ForeignKey("networking_qos_policies.id", ondelete="CASCADE"))
    max_kbps:          Mapped[int] = mapped_column(Integer)       # kilobits per second
    max_burst_kbps:    Mapped[int] = mapped_column(Integer, default=0)
    direction:         Mapped[str] = mapped_column(String, default="egress")  # egress | ingress
    created_at:        Mapped[datetime] = mapped_column(DateTime, default=now)

class SubnetPool(Base):
    """Pre-defined CIDR pool from which subnets can be auto-allocated."""
    __tablename__ = "networking_subnet_pools"
    id:              Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name:            Mapped[str] = mapped_column(String, unique=True, index=True)
    prefixes:        Mapped[str] = mapped_column(String)       # comma-separated CIDR list
    default_prefixlen: Mapped[int] = mapped_column(Integer, default=24)
    min_prefixlen:   Mapped[int] = mapped_column(Integer, default=8)
    max_prefixlen:   Mapped[int] = mapped_column(Integer, default=32)
    shared:          Mapped[bool] = mapped_column(Boolean, default=False)
    project_id:      Mapped[str | None] = mapped_column(String, nullable=True)
    created_at:      Mapped[datetime] = mapped_column(DateTime, default=now)

class Trunk(Base):
    """Trunk port — a parent port that carries multiple VLANs via sub-ports."""
    __tablename__ = "networking_trunks"
    id:          Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name:        Mapped[str] = mapped_column(String, unique=True, index=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    port_id:     Mapped[str] = mapped_column(String, ForeignKey("networking_ports.id", ondelete="CASCADE"))
    status:      Mapped[str] = mapped_column(String, default="ACTIVE")
    project_id:  Mapped[str | None] = mapped_column(String, nullable=True)
    created_at:  Mapped[datetime] = mapped_column(DateTime, default=now)

class TrunkSubPort(Base):
    """Sub-port entry that associates a VLAN-tagged port with a trunk."""
    __tablename__ = "networking_trunk_subports"
    id:              Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    trunk_id:        Mapped[str] = mapped_column(String, ForeignKey("networking_trunks.id", ondelete="CASCADE"))
    port_id:         Mapped[str] = mapped_column(String, ForeignKey("networking_ports.id", ondelete="CASCADE"))
    segmentation_type: Mapped[str] = mapped_column(String, default="vlan")  # vlan | vxlan
    segmentation_id:   Mapped[int] = mapped_column(Integer)                 # VLAN ID 1-4094
    created_at:       Mapped[datetime] = mapped_column(DateTime, default=now)
