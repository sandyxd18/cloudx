import ipaddress
import logging
import os
import secrets
import subprocess
import sys
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, create_engine, inspect, or_, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

import sys, os
sys.path.insert(0, "/app")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

try:
    from shared.auth_middleware import CurrentUser, AdminUser, OptionalAuth
except ImportError:
    try:
        from app.auth_middleware import CurrentUser, AdminUser, OptionalAuth
    except ImportError:
        from auth_middleware import CurrentUser, AdminUser, OptionalAuth

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
try:
    from shared.request_id_middleware import RequestIDMiddleware
except ImportError:
    RequestIDMiddleware = None

try:
    from shared.rpc import RPCClient as _RPCClientClass
except ImportError:
    _RPCClientClass = None

RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://cloudx:cloudx@rabbitmq:5672/")
log = logging.getLogger("networking-api")
_rpc = None  # lazily initialised on first event publish


def _publish_net_event(event_type: str, payload: dict) -> None:
    """Best-effort event publishing — never raises, never blocks."""
    global _rpc
    if _RPCClientClass is None:
        return
    try:
        if _rpc is None:
            _rpc = _RPCClientClass(RABBITMQ_URL)
        _rpc.publish_event(event_type, payload)
    except Exception as exc:
        log.debug("Event publish failed (non-critical): %s", exc)


DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://mini:mini@postgres:5432/mini_openstack")
OVN_NB_DB = os.getenv("OVN_NB_DB", "tcp:ovn-nb-db:6641")
PROVIDER_INTERFACE = os.getenv("PROVIDER_INTERFACE", "enp0s8")
INTERNAL_VIP_ADDRESS = os.getenv("INTERNAL_VIP_ADDRESS", "")


PROVIDER_BRIDGE = "br-ex"
PROVIDER_PHYSNET = "physnet1"
# Strip CIDR notation if present (e.g. "192.168.10.102/24" → "192.168.10.102")
PROVIDER_HOST_IP_RAW = INTERNAL_VIP_ADDRESS.split("/")[0].strip() if INTERNAL_VIP_ADDRESS else ""

METADATA_LINK_LOCAL = "169.254.169.254"
DEFAULT_SECURITY_GROUP_ALLOW_SSH = os.getenv("DEFAULT_SECURITY_GROUP_ALLOW_SSH", "true").lower() == "true"
DEFAULT_SECURITY_GROUP_ALLOW_ICMP = os.getenv("DEFAULT_SECURITY_GROUP_ALLOW_ICMP", "true").lower() == "true"

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


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
    """QoS policy (Neutron QoS extension)."""
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


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


DB = Annotated[Session, Depends(get_db)]


def obj_to_dict(obj):
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}


def _fmt_ts(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _ip_version(cidr: str) -> int:
    try:
        return ipaddress.ip_network(cidr, strict=False).version
    except Exception:
        return 4


# ── OpenStack-compatible serializers ───────────────────────────────────────────

def network_to_os_dict(db: Session, network: Network, current: dict | None = None) -> dict:
    """Serialize a Network to an OpenStack-compatible dict."""
    # Collect subnet IDs for this network
    subnet_ids = [s.id for s in db.scalars(
        select(Subnet).where(Subnet.network_id == network.id).order_by(Subnet.created_at)
    ).all()]

    router_external_str = "External" if network.external else "Internal"

    return {
        "admin_state_up": "UP",
        "availability_zone_hints": [],
        "availability_zones": [],
        "created_at": _fmt_ts(network.created_at),
        "description": "",
        "dns_domain": None,
        "id": network.id,
        "ipv4_address_scope": None,
        "ipv6_address_scope": None,
        "is_default": True if network.external else None,
        "is_vlan_transparent": False,
        "mtu": 1500 if network.external else 1442,
        "name": network.name,
        "port_security_enabled": True,
        "project_id": current.get("project_id") if current else None,
        "provider:network_type": network.network_type or ("flat" if network.external else "geneve"),
        "provider:physical_network": network.physical_network,
        "provider:segmentation_id": None,
        "qos_policy_id": None,
        "revision_number": 3,
        "router:external": router_external_str,
        "segments": None,
        "shared": bool(network.shared),
        "status": "ACTIVE",
        "subnets": subnet_ids,
        "tags": [],
        "updated_at": _fmt_ts(network.created_at),
    }


def subnet_to_os_dict(db: Session, subnet: Subnet, current: dict | None = None) -> dict:
    """Serialize a Subnet to an OpenStack-compatible dict."""
    network = db.get(Network, subnet.network_id)
    allocation_pool = None
    if subnet.allocation_start and subnet.allocation_end:
        allocation_pool = f"{subnet.allocation_start}-{subnet.allocation_end}"
    elif subnet.allocation_start:
        allocation_pool = subnet.allocation_start
    dns = [s.strip() for s in subnet.dns_nameservers.split(",") if s.strip()] if subnet.dns_nameservers else []

    return {
        "allocation_pools": allocation_pool or "",
        "cidr": subnet.cidr,
        "created_at": _fmt_ts(subnet.created_at),
        "description": "",
        "dns_nameservers": dns,
        "dns_publish_fixed_ip": None,
        "enable_dhcp": subnet.enable_dhcp,
        "gateway_ip": subnet.gateway_ip or subnet.gateway,
        "host_routes": [],
        "id": subnet.id,
        "ip_version": _ip_version(subnet.cidr),
        "ipv6_address_mode": None,
        "ipv6_ra_mode": None,
        "name": subnet.name,
        "network_id": subnet.network_id,
        "project_id": current.get("project_id") if current else None,
        "revision_number": 0,
        "router:external": bool(network.external) if network else False,
        "segment_id": None,
        "service_types": [],
        "subnetpool_id": None,
        "tags": [],
        "updated_at": _fmt_ts(subnet.created_at),
    }


def router_to_os_dict(db: Session, router: Router, current: dict | None = None) -> dict:
    """Serialize a Router to an OpenStack-compatible dict."""
    # Build external_gateway_info
    ext_gateway_info = None
    if router.external_network_id:
        ext_net = db.get(Network, router.external_network_id)
        ext_subnet = None
        ext_ip = None
        if ext_net:
            ext_subnet = db.scalar(select(Subnet).where(Subnet.network_id == ext_net.id).order_by(Subnet.created_at))
        if ext_subnet:
            # Find the gateway port (lrp external port) via RouterInterface or directly from network's gateway_ip
            ext_ip = ext_net.gateway_ip if ext_net else None
        ext_gateway_info = {
            "network_id": router.external_network_id,
            "external_fixed_ips": (
                [{"subnet_id": ext_subnet.id, "ip_address": ext_ip}]
                if ext_subnet and ext_ip else []
            ),
            "enable_snat": True,
        }

    # Build interfaces_info
    interfaces = db.scalars(
        select(RouterInterface).where(RouterInterface.router_id == router.id)
    ).all()
    interfaces_info = [
        {
            "port_id": iface.router_port_name,
            "ip_address": iface.ip_address.split("/")[0] if iface.ip_address else None,
            "subnet_id": iface.subnet_id,
        }
        for iface in interfaces
    ]

    return {
        "admin_state_up": "UP",
        "availability_zone_hints": [],
        "availability_zones": [],
        "created_at": _fmt_ts(router.created_at),
        "description": "",
        "enable_default_route_bfd": False,
        "enable_default_route_ecmp": False,
        "enable_snat": True,
        "external_gateway_info": ext_gateway_info,
        "flavor_id": None,
        "ha": False,
        "id": router.id,
        "interfaces_info": interfaces_info,
        "name": router.name,
        "project_id": current.get("project_id") if current else None,
        "revision_number": 4,
        "routes": [],
        "status": "ACTIVE",
        "tags": [],
        "updated_at": _fmt_ts(router.created_at),
    }


def port_to_os_dict(db: Session, port: Port, current: dict | None = None) -> dict:
    """Serialize a Port to an OpenStack-compatible dict."""
    network = db.get(Network, port.network_id)
    subnet = db.get(Subnet, port.subnet_id) if port.subnet_id else None

    ip_addr = port.ip_address or port.fixed_ip
    fixed_ips = []
    if ip_addr and port.subnet_id:
        fixed_ips.append({"ip_address": ip_addr, "subnet_id": port.subnet_id})
    elif ip_addr:
        fixed_ips.append({"ip_address": ip_addr})
    # Dual-stack: include IPv6 if present
    if port.ipv6_address:
        fixed_ips.append({"ip_address": port.ipv6_address, "ip_version": 6})

    sg_ids = [item for item in (port.security_group_ids or "").split(",") if item]

    # Build dns_assignment if dns_name is set
    dns_assignment = []
    if port.dns_name:
        dns_assignment = [{"hostname": port.dns_name, "ip_address": ip_addr, "fqdn": f"{port.dns_name}."}]

    return {
        "admin_state_up": "UP",
        "allowed_address_pairs": [],
        "binding_host_id": "",
        "binding_profile": {},
        "binding_vif_details": {},
        "binding_vif_type": "ovs" if port.status == "ACTIVE" else "unbound",
        "binding_vnic_type": "normal",
        "created_at": _fmt_ts(port.created_at),
        "data_plane_status": None,
        "description": "",
        "device_id": port.device_id or "",
        "device_owner": port.device_owner or "",
        "device_profile": None,
        "dns_assignment": dns_assignment,
        "dns_domain": None,
        "dns_name": port.dns_name,
        "extra_dhcp_opts": [],
        "fixed_ips": fixed_ips,
        "hardware_offload_type": None,
        "hints": {},
        "id": port.id,
        "ip_allocation": None,
        "mac_address": port.mac_address,
        "name": port.name,
        "network_id": port.network_id,
        "numa_affinity_policy": None,
        "port_security_enabled": port.port_security_enabled,
        "project_id": current.get("project_id") if current else None,
        "propagate_uplink_status": None,
        "resource_request": None,
        "revision_number": 2,
        "qos_network_policy_id": None,
        "qos_policy_id": None,
        "security_group_ids": sg_ids,
        "status": port.status,
        "tags": [],
        "trunk_details": None,
        "trusted": None,
        "updated_at": _fmt_ts(port.created_at),
        # Internal fields kept for compatibility
        "ip_address": ip_addr,
        "fixed_ip": ip_addr,
        "ipv6_address": port.ipv6_address,
        "logical_switch_name": port.logical_switch_name or (network.name if network else port.network_id),
        "logical_port_name": port.logical_port_name or port.name,
        "cidr": subnet.cidr if subnet else None,
        "gateway_ip": (subnet.gateway_ip or subnet.gateway) if subnet else None,
        "dns_nameservers": ([s.strip() for s in subnet.dns_nameservers.split(",") if s.strip()] if subnet and subnet.dns_nameservers else []),
        "subnet_id": port.subnet_id,
    }


def fip_to_os_dict(db: Session, fip: FloatingIP, current: dict | None = None) -> dict:
    """Serialize a FloatingIP to an OpenStack-compatible dict."""
    return {
        "created_at": _fmt_ts(fip.created_at),
        "description": "",
        "dns_domain": None,
        "dns_name": None,
        "fixed_ip_address": fip.fixed_ip_address,
        "floating_ip_address": fip.floating_ip_address or fip.floating_ip,
        "floating_network_id": fip.external_network_id,
        "id": fip.id,
        "name": fip.name,
        "port_details": None,
        "port_id": fip.port_id or fip.fixed_port_id,
        "project_id": current.get("project_id") if current else None,
        "qos_policy_id": None,
        "revision_number": 0,
        "router_id": fip.router_id,
        "status": "ACTIVE" if (fip.port_id or fip.fixed_port_id or fip.fixed_ip_address or fip.server_id) else "DOWN",
        "tags": [],
        "updated_at": _fmt_ts(fip.created_at),
        # Kept for backward compat
        "floating_ip": fip.floating_ip,
        "external_network_id": fip.external_network_id,
        "server_id": fip.server_id,
    }


def sg_to_os_dict(db: Session, group: SecurityGroup, current: dict | None = None, include_rules: bool = True) -> dict:
    """Serialize a SecurityGroup to an OpenStack-compatible dict."""
    rules = []
    if include_rules:
        raw_rules = db.scalars(
            select(SecurityGroupRule).where(SecurityGroupRule.security_group_id == group.id)
            .order_by(SecurityGroupRule.created_at)
        ).all()
        rules = [
            {
                "id": r.id,
                "security_group_id": r.security_group_id,
                "direction": r.direction,
                "ethertype": r.ethertype or "IPv4",
                "protocol": None if r.protocol == "any" else r.protocol,
                "port_range_min": int(r.port_min) if r.port_min else None,
                "port_range_max": int(r.port_max) if r.port_max else None,
                "remote_ip_prefix": r.remote_ip_prefix,
                "remote_group_id": r.remote_group_id,
                "remote_address_group_id": None,
                "description": "",
                "created_at": _fmt_ts(r.created_at),
                "updated_at": _fmt_ts(r.created_at),
                "revision_number": 0,
            }
            for r in raw_rules
        ]
    return {
        "created_at": _fmt_ts(group.created_at),
        "description": group.description or "",
        "id": group.id,
        "name": group.name,
        "project_id": group.project_id or (current.get("project_id") if current else None),
        "revision_number": 1,
        "security_group_rules": rules,
        "shared": False,
        "stateful": True,
        "tags": [],
        "updated_at": _fmt_ts(group.updated_at or group.created_at),
    }


def port_to_dict(db: Session, port: Port, current: dict | None = None) -> dict:
    """Alias that returns OS-compatible port dict (replaces old port_to_dict)."""
    return port_to_os_dict(db, port, current)



def get_by_ref(db: Session, model, ref: str):
    obj = db.get(model, ref)
    if obj:
        return obj
    return db.scalar(select(model).where(model.name == ref))


def get_floating_ip(db: Session, ref: str):
    return (
        db.get(FloatingIP, ref)
        or db.scalar(select(FloatingIP).where(FloatingIP.name == ref))
        or db.scalar(select(FloatingIP).where(FloatingIP.floating_ip == ref))
        or db.scalar(select(FloatingIP).where(FloatingIP.floating_ip_address == ref))
    )


def require_floating_ip(db: Session, ref: str):
    fip = get_floating_ip(db, ref)
    if not fip:
        raise HTTPException(status_code=404, detail=f"floating-ip '{ref}' not found")
    return fip


def require_by_ref(db: Session, model, ref: str, label: str):
    obj = get_by_ref(db, model, ref)
    if not obj:
        raise HTTPException(status_code=404, detail=f"{label} '{ref}' not found")
    return obj


def ovn(*args: str, check: bool = True) -> str:
    cmd = ["ovn-nbctl", f"--db={OVN_NB_DB}", "--timeout=8", *args]
    proc = subprocess.run(cmd, text=True, capture_output=True)
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
    proc = subprocess.run(cmd, text=True, capture_output=True)
    if check and proc.returncode != 0:
        raise HTTPException(status_code=502, detail={"message": "ovs-vsctl failed", "stderr": proc.stderr.strip()})
    return proc.stdout.strip()


def get_local_chassis_id() -> str | None:
    """Get the chassis ID of the local OVN chassis from SB DB (the node running ovn-controller)."""
    try:
        OVN_SB_DB = os.environ.get("OVN_SB_DB", "tcp:ovn-sb-db:6642")
        cmd = ["ovn-sbctl", f"--db={OVN_SB_DB}", "--timeout=8", "--format=list", "--columns=name", "find", "Chassis"]
        proc = subprocess.run(cmd, text=True, capture_output=True)
        if proc.returncode != 0 or not proc.stdout.strip():
            return None
        # Returns lines like: name : "ddec5957-..."
        # Must strip surrounding quotes from the value (ovn-sbctl --format=list wraps strings in "")
        for line in proc.stdout.strip().splitlines():
            if line.startswith("name"):
                raw = line.split(":", 1)[-1].strip()
                return raw.strip('"')  # Remove surrounding quotes e.g. "uuid" → uuid
        return None
    except Exception as exc:
        print(f"get_local_chassis_id failed: {exc}", flush=True)
        return None





def localnet_port_name(network_name: str) -> str:
    return f"{network_name}-localnet"


def localnet_commands(network_name: str, physnet: str) -> list[list[str]]:
    port_name = localnet_port_name(network_name)
    return [
        ["--may-exist", "lsp-add", network_name, port_name],
        ["lsp-set-type", port_name, "localnet"],
        ["lsp-set-addresses", port_name, "unknown"],
        ["lsp-set-options", port_name, f"network_name={physnet}"],
        ["set", "logical_switch_port", port_name, "external_ids:mini-openstack-lab=true", "external_ids:provider-localnet=true"],
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


PROVIDER_BRIDGE = os.getenv("PROVIDER_BRIDGE", "br-ex")
PROVIDER_PHYSNET = os.getenv("PROVIDER_PHYSNET", "physnet1")
PROVIDER_INTERFACE = os.getenv("PROVIDER_INTERFACE", "enp0s8")
INTERNAL_VIP_ADDRESS = os.getenv("INTERNAL_VIP_ADDRESS", "192.168.18.80/24")

def external_network_defaults(name: str) -> dict:
    cidr = "192.168.18.0/24"
    gateway = "192.168.18.1"
    if "/" in INTERNAL_VIP_ADDRESS:
        import ipaddress
        try:
            net = ipaddress.ip_network(INTERNAL_VIP_ADDRESS, strict=False)
            cidr = str(net)
            gateway = str(net.network_address + 1)
        except Exception:
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
        "allocation_start": os.getenv("PROVIDER_ALLOCATION_START") or "192.168.18.200",
        "allocation_end": os.getenv("PROVIDER_ALLOCATION_END") or "192.168.18.220",
    }

def allocation_bounds_for_network(cidr: str, gateway_ip: str, start: str | None = None, end: str | None = None) -> tuple[str, str]:
    if start and end:
        return start, end
    import ipaddress
    try:
        net = ipaddress.ip_network(cidr, strict=False)
        hosts = list(net.hosts())
        s = start or (str(hosts[199]) if len(hosts) > 199 else str(hosts[1]))
        e = end or (str(hosts[-2]) if len(hosts) > 2 else str(hosts[-1]))
        return s, e
    except Exception:
        return start or "192.168.18.200", end or "192.168.18.220"

def ensure_provider_ovn(network_name: str, physnet: str, bridge: str) -> None:
    ovn("--may-exist", "ls-add", network_name)
    ovn(
        "set",
        "logical_switch",
        network_name,
        "external_ids:mini-openstack-lab=true",
        "external_ids:network_type=external",
        f"external_ids:physical_network={physnet}",
        f"external_ids:bridge_mapping={physnet}:{bridge}",
    )
    for command in localnet_commands(network_name, physnet):
        ovn(*command)
    ensure_ovn_bridge_mapping(physnet, bridge)


def subnet_ip_with_prefix(subnet: Subnet) -> str:
    net = ipaddress.ip_network(subnet.cidr, strict=False)
    return f"{subnet.gateway_ip or subnet.gateway}/{net.prefixlen}"


def subnet_dns(subnet: Subnet) -> list[str]:
    if not subnet.dns_nameservers:
        return []
    return [item.strip() for item in subnet.dns_nameservers.split(",") if item.strip()]


def parse_id_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [item for item in value.split(",") if item]


def format_id_list(values: list[str]) -> str | None:
    return ",".join(values) if values else None


def allocation_bounds(subnet: Subnet) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Return (start, end) IP allocation bounds for an IPv4 or IPv6 subnet."""
    network = ipaddress.ip_network(subnet.cidr, strict=False)
    hosts = list(network.hosts())
    if not hosts:
        raise HTTPException(status_code=400, detail=f"subnet '{subnet.name}' has no usable host addresses")

    if network.version == 6:
        # IPv6: use network+1 to network.broadcast_address-1
        start = ipaddress.ip_address(subnet.allocation_start) if subnet.allocation_start else hosts[0]
        end = ipaddress.ip_address(subnet.allocation_end) if subnet.allocation_end else hosts[-1]
    else:
        start = ipaddress.ip_address(subnet.allocation_start) if subnet.allocation_start else hosts[1 if len(hosts) > 1 else 0]
        end = ipaddress.ip_address(subnet.allocation_end) if subnet.allocation_end else hosts[-1]

    if start not in network or end not in network or start > end:
        raise HTTPException(status_code=400, detail="invalid allocation range for subnet")
    return start, end


def allocation_bounds_for_network(cidr_value: str, gateway_value: str | None, start_value: str | None, end_value: str | None) -> tuple[str, str]:
    network = ipaddress.ip_network(cidr_value, strict=False)
    hosts = list(network.hosts())
    if not hosts:
        raise HTTPException(status_code=400, detail=f"network {cidr_value} has no usable addresses")
    gateway_ip = ipaddress.ip_address(gateway_value) if gateway_value else hosts[0]
    start = ipaddress.ip_address(start_value) if start_value else next((host for host in hosts if host != gateway_ip), None)
    end = ipaddress.ip_address(end_value) if end_value else hosts[-1]
    if start is None:
        raise HTTPException(status_code=400, detail="no usable allocation start")
    if gateway_ip not in network or start not in network or end not in network or start > end:
        raise HTTPException(status_code=400, detail="invalid allocation range")
    return str(start), str(end)


def dhcp_options_args(cidr: str, gateway: str, dns_nameservers: list[str], server_mac: str = "02:00:00:00:00:01") -> list[str]:
    args = [
        "create",
        "DHCP_Options",
        f"cidr={cidr}",
        "external_ids:mini-openstack-lab=true",
        f"options:server_id={gateway}",
        f"options:router={gateway}",
        f"options:server_mac={server_mac}",
        "options:lease_time=3600",
    ]
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
            "external_ids:mini-openstack-lab=true",
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


def allocate_floating_ip(db: Session, network: Network) -> str:
    if not network.provider_cidr:
        raise HTTPException(status_code=400, detail="external network has no provider_cidr")
    start, end = allocation_bounds_for_network(network.provider_cidr, network.gateway_ip, network.allocation_start, network.allocation_end)
    current = ipaddress.ip_address(start)
    end_ip = ipaddress.ip_address(end)
    used = {
        ipaddress.ip_address(ip)
        for ip in db.scalars(select(FloatingIP.floating_ip)).all()
        if ip
    }
    used.update(
        ipaddress.ip_address(ip)
        for ip in db.scalars(select(FloatingIP.floating_ip_address)).all()
        if ip
    )
    while current <= end_ip:
        if str(current) != network.gateway_ip and current not in used:
            return str(current)
        current += 1
    raise HTTPException(status_code=409, detail=f"no available floating IPs in external network '{network.name}'")


def find_router_for_fip(db: Session, router_ref: str | None) -> Router:
    if router_ref:
        return require_by_ref(db, Router, router_ref, "router")
    router = db.scalar(select(Router).where(Router.external_network_id.is_not(None)).order_by(Router.created_at))
    if not router:
        raise HTTPException(status_code=409, detail="no router with external gateway; run router set-gateway first")
    return router


def router_external_port_names(router_name: str) -> tuple[str, str]:
    return f"lrp-{router_name}-external", f"lsp-{router_name}-external"


def ensure_router_gateway_ovn(router: Router, network: Network) -> None:
    if not network.gateway_ip or not network.provider_cidr:
        raise HTTPException(status_code=400, detail="external network is missing provider gateway/cidr")
    cidr = ipaddress.ip_network(network.provider_cidr, strict=False)
    lrp, lsp = router_external_port_names(router.name)
    port_mac = stable_mac(f"{router.id}-{network.id}-external")
    ovn("--may-exist", "lrp-add", router.name, lrp, port_mac, f"{network.gateway_ip}/{cidr.prefixlen}")
    ovn("set", "logical_router_port", lrp, "external_ids:mini-openstack-lab=true", "external_ids:external-gateway=true")
    ovn("--may-exist", "lsp-add", network.name, lsp)
    ovn("set", "logical_switch_port", lsp, "external_ids:mini-openstack-lab=true", "external_ids:external-gateway=true")
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


def resolve_association_port(db: Session, payload: FloatingIPAssociateIn) -> Port:
    if payload.port:
        return require_by_ref(db, Port, payload.port, "port")
    if payload.server:
        # payload.server may be a server UUID (Port.device_id) or a server
        # NAME (e.g. "test1") — compute ports follow the
        # "{server_name}-{server_id[:8]}-port" naming convention.
        port = db.scalar(select(Port).where(Port.device_id == payload.server).order_by(Port.created_at))
        if not port:
            port = db.scalar(
                select(Port).where(
                    Port.device_owner.like("compute:%"),
                    Port.name.like(f"{payload.server}-%-port"),
                ).order_by(Port.created_at)
            )
        if port:
            return port
        raise HTTPException(status_code=404, detail=f"port for server '{payload.server}' not found")
    raise HTTPException(status_code=400, detail="floating IP association requires --server or --port")


def nat_rule_args(router_name: str, external_ip: str, logical_ip: str) -> list[str]:
    return ["--may-exist", "lr-nat-add", router_name, "dnat_and_snat", external_ip, logical_ip]


def port_group_name(security_group_id: str) -> str:
    return f"pg_{security_group_id.replace('-', '')}"


def acl_match(security_group_id: str, rule: SecurityGroupRule) -> str:
    port_field = "outport" if rule.direction == "ingress" else "inport"
    remote_field = "ip4.src" if rule.direction == "ingress" else "ip4.dst"
    parts = [f"{port_field} == @{port_group_name(security_group_id)}", "ip4"]
    protocol = rule.protocol or "any"
    if protocol == "icmp":
        parts.append("icmp4")
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
    ovn("set", "port_group", pg, "external_ids:mini-openstack-lab=true", f"external_ids:security_group_id={group.id}", f"external_ids:name={group.name}", check=False)
    return pg


def apply_security_group_acls(db: Session, group: SecurityGroup) -> None:
    pg = ensure_security_group_port_group(group)
    ovn("--type=port-group", "--if-exists", "acl-del", pg, check=False)
    rules = db.scalars(select(SecurityGroupRule).where(SecurityGroupRule.security_group_id == group.id)).all()
    for rule in rules:
        direction = "to-lport" if rule.direction == "ingress" else "from-lport"
        action = "allow-related" if rule.action == "allow" else rule.action
        ovn("--type=port-group", "--may-exist", "acl-add", pg, direction, acl_priority(rule), acl_match(group.id, rule), action)
    ovn("--type=port-group", "--may-exist", "acl-add", pg, "to-lport", "1001", f"outport == @{pg} && ip4", "drop")


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


def allocate_fixed_ip(db: Session, subnet: Subnet, requested: str | None = None) -> str:
    network = ipaddress.ip_network(subnet.cidr, strict=False)
    gateway_str = subnet.gateway_ip or subnet.gateway
    gateway_ip = ipaddress.ip_address(gateway_str) if gateway_str else None
    used = {
        ipaddress.ip_address(ip)
        for ip in db.scalars(select(Port.fixed_ip).where(Port.subnet_id == subnet.id, Port.fixed_ip.is_not(None))).all()
    }
    used.update(
        ipaddress.ip_address(ip)
        for ip in db.scalars(select(Port.ip_address).where(Port.subnet_id == subnet.id, Port.ip_address.is_not(None))).all()
    )
    if requested:
        candidate = ipaddress.ip_address(requested)
        if candidate not in network:
            raise HTTPException(status_code=400, detail=f"requested IP {requested} is outside subnet {subnet.cidr}")
        if (gateway_ip and candidate == gateway_ip) or candidate in used:
            raise HTTPException(status_code=409, detail=f"requested IP {requested} is not available")
        return str(candidate)
    start, end = allocation_bounds(subnet)
    current = start
    while current <= end:
        if (gateway_ip is None or current != gateway_ip) and current not in used:
            return str(current)
        current += 1
    raise HTTPException(status_code=409, detail=f"no available IP addresses in subnet '{subnet.name}'")


def ensure_columns():
    inspector = inspect(engine)
    table_names = inspector.get_table_names()
    with engine.begin() as conn:
        if "networking_networks" in table_names:
            columns = {column["name"] for column in inspector.get_columns("networking_networks")}
            for column, ddl in (
                ("external", "BOOLEAN DEFAULT FALSE"),
                ("shared", "BOOLEAN DEFAULT FALSE"),
                ("provider", "BOOLEAN DEFAULT FALSE"),
                ("network_type", "VARCHAR"),
                ("physical_network", "VARCHAR"),
                ("bridge_mapping", "VARCHAR"),
                ("provider_bridge", "VARCHAR"),
                ("provider_cidr", "VARCHAR"),
                ("gateway_ip", "VARCHAR"),
                ("allocation_start", "VARCHAR"),
                ("allocation_end", "VARCHAR"),
                ("project_id", "VARCHAR"),
            ):
                if column not in columns:
                    conn.execute(text(f"ALTER TABLE networking_networks ADD COLUMN {column} {ddl}"))
            conn.execute(text("UPDATE networking_networks SET shared = FALSE WHERE shared IS NULL"))
        if "networking_subnets" in table_names:
            columns = {column["name"] for column in inspector.get_columns("networking_subnets")}
            for column in ("gateway_ip", "allocation_start", "allocation_end", "dns_nameservers"):
                if column not in columns:
                    conn.execute(text(f"ALTER TABLE networking_subnets ADD COLUMN {column} VARCHAR"))
            for column, ddl in (
                ("enable_dhcp", "BOOLEAN DEFAULT TRUE"),
                ("ovn_dhcp_options_uuid", "VARCHAR"),
                ("ip_version", "INTEGER DEFAULT 4"),
                ("ipv6_ra_mode", "VARCHAR"),
                ("ipv6_address_mode", "VARCHAR"),
            ):
                if column not in columns:
                    conn.execute(text(f"ALTER TABLE networking_subnets ADD COLUMN {column} {ddl}"))
            conn.execute(text("UPDATE networking_subnets SET gateway_ip = gateway WHERE gateway_ip IS NULL"))
            conn.execute(text("UPDATE networking_subnets SET ovn_dhcp_options_uuid = dhcp_options_id WHERE ovn_dhcp_options_uuid IS NULL"))
        if "networking_ports" in table_names:
            columns = {column["name"] for column in inspector.get_columns("networking_ports")}
            for column in (
                "subnet_id",
                "fixed_ip",
                "logical_switch_name",
                "logical_port_name",
                "device_id",
                "device_owner",
                "security_group_ids",
            ):
                if column not in columns:
                    conn.execute(text(f"ALTER TABLE networking_ports ADD COLUMN {column} VARCHAR"))
            if "port_security_enabled" not in columns:
                conn.execute(text("ALTER TABLE networking_ports ADD COLUMN port_security_enabled BOOLEAN DEFAULT TRUE"))
            conn.execute(text("UPDATE networking_ports SET fixed_ip = ip_address WHERE fixed_ip IS NULL AND ip_address IS NOT NULL"))
        if "networking_routers" in table_names:
            columns = {column["name"] for column in inspector.get_columns("networking_routers")}
            if "external_network_id" not in columns:
                conn.execute(text("ALTER TABLE networking_routers ADD COLUMN external_network_id VARCHAR"))
        if "networking_floating_ips" in table_names:
            columns = {column["name"] for column in inspector.get_columns("networking_floating_ips")}
            for column in (
                "floating_ip_address",
                "fixed_ip_address",
                "port_id",
                "server_id",
                "external_network_id",
                "router_id",
            ):
                if column not in columns:
                    conn.execute(text(f"ALTER TABLE networking_floating_ips ADD COLUMN {column} VARCHAR"))
            conn.execute(text("UPDATE networking_floating_ips SET floating_ip_address = floating_ip WHERE floating_ip_address IS NULL"))
            conn.execute(text("UPDATE networking_floating_ips SET port_id = fixed_port_id WHERE port_id IS NULL"))
        if "networking_security_groups" in table_names:
            columns = {column["name"] for column in inspector.get_columns("networking_security_groups")}
            if "project_id" not in columns:
                conn.execute(text("ALTER TABLE networking_security_groups ADD COLUMN project_id VARCHAR"))
            if "updated_at" not in columns:
                conn.execute(text("ALTER TABLE networking_security_groups ADD COLUMN updated_at TIMESTAMP"))
            conn.execute(text("UPDATE networking_security_groups SET updated_at = created_at WHERE updated_at IS NULL"))
        if "networking_ports" in table_names:
            port_cols = {column["name"] for column in inspector.get_columns("networking_ports")}
            if "qos_policy_id" not in port_cols:
                conn.execute(text("ALTER TABLE networking_ports ADD COLUMN qos_policy_id VARCHAR"))
        if "networking_security_group_rules" in table_names:
            columns = {column["name"] for column in inspector.get_columns("networking_security_group_rules")}
            if "remote_group_id" not in columns:
                conn.execute(text("ALTER TABLE networking_security_group_rules ADD COLUMN remote_group_id VARCHAR"))


def bootstrap_default_security_group() -> None:
    db = SessionLocal()
    try:
        group = get_by_ref(db, SecurityGroup, "default")
        if not group:
            group = SecurityGroup(name="default", description="Default lab security group", project_id="default")
            db.add(group)
            db.commit()
            db.refresh(group)
        ensure_security_group_port_group(group)

        def ensure_rule(direction: str, protocol: str, port_min: int | None = None, remote_ip_prefix: str | None = None):
            existing = db.scalar(
                select(SecurityGroupRule).where(
                    SecurityGroupRule.security_group_id == group.id,
                    SecurityGroupRule.direction == direction,
                    SecurityGroupRule.protocol == protocol,
                    SecurityGroupRule.port_min == (str(port_min) if port_min is not None else None),
                    SecurityGroupRule.remote_ip_prefix == remote_ip_prefix,
                )
            )
            if existing:
                return
            db.add(
                SecurityGroupRule(
                    security_group_id=group.id,
                    direction=direction,
                    protocol=protocol,
                    port_min=str(port_min) if port_min is not None else None,
                    port_max=str(port_min) if port_min is not None else None,
                    remote_ip_prefix=remote_ip_prefix,
                    action="allow",
                )
            )

        ensure_rule("egress", "any", remote_ip_prefix="0.0.0.0/0")
        if DEFAULT_SECURITY_GROUP_ALLOW_SSH:
            ensure_rule("ingress", "tcp", 22, "0.0.0.0/0")
        if DEFAULT_SECURITY_GROUP_ALLOW_ICMP:
            ensure_rule("ingress", "icmp", remote_ip_prefix="0.0.0.0/0")
        group.updated_at = now()
        db.commit()
        db.refresh(group)
        apply_security_group_acls(db, group)
    finally:
        db.close()



app = FastAPI(title="cloudx networking-api", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
if RequestIDMiddleware:
    app.add_middleware(RequestIDMiddleware)

try:
    from shared.metrics import setup_metrics
    setup_metrics(app, "networking-api")
except Exception:
    pass


@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)
    ensure_columns()
    try:
        bootstrap_default_security_group()
    except Exception as exc:
        print(f"default security group bootstrap skipped: {exc}", flush=True)


@app.get("/health")
def health():
    return {"status": "ok", "service": "networking-api"}


@app.get("/healthcheck")
def healthcheck(db: DB):
    """Standard CloudX health check endpoint.
    Checks: database connectivity.
    Returns: {status, checks: {db}} compatible with OpenStack-style health format.
    """
    checks = {}
    overall = "up"
    # DB check
    try:
        db.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as exc:
        checks["db"] = f"error: {exc}"
        overall = "degraded"
    return {"status": overall, "checks": checks}



@app.post("/networks", status_code=201)
def create_network(payload: NetworkIn, current: CurrentUser, db: DB):
    if get_by_ref(db, Network, payload.name):
        raise HTTPException(status_code=409, detail="network already exists")
    provider_defaults = external_network_defaults(payload.name) if payload.external else {}
    allocation_start = provider_defaults.get("allocation_start")
    allocation_end = provider_defaults.get("allocation_end")
    if payload.external:
        allocation_start, allocation_end = allocation_bounds_for_network(provider_defaults["provider_cidr"], provider_defaults["gateway_ip"], allocation_start, allocation_end)
    network = Network(
        name=payload.name,
        external=payload.external,
        provider=provider_defaults.get("provider", False),
        network_type="external" if payload.external else "geneve",
        physical_network=provider_defaults.get("physical_network"),
        bridge_mapping=provider_defaults.get("bridge_mapping"),
        provider_bridge=provider_defaults.get("provider_bridge"),
        provider_cidr=provider_defaults.get("provider_cidr"),
        gateway_ip=provider_defaults.get("gateway_ip"),
        allocation_start=allocation_start,
        allocation_end=allocation_end,
        project_id=current.get("project_id"),
    )
    if payload.external:
        ensure_provider_ovn(payload.name, provider_defaults["physical_network"], provider_defaults["provider_bridge"])
    else:
        ovn("--may-exist", "ls-add", payload.name)
        ovn("set", "logical_switch", payload.name, "external_ids:mini-openstack-lab=true")
    db.add(network)
    db.commit()
    db.refresh(network)
    _publish_net_event("network.create.end", {
        "network_id": network.id,
        "name":       network.name,
        "external":   network.external,
    })
    return obj_to_dict(network)


@app.get("/networks")
def list_networks(current: CurrentUser, db: DB):
    project_id = current.get("project_id")
    if current.get("role") == "admin":
        # Admin sees all networks
        q = select(Network).order_by(Network.created_at)
    else:
        # Non-admin sees: own project's networks + shared networks
        from sqlalchemy import or_
        q = select(Network).where(
            or_(
                Network.project_id == project_id,
                Network.shared == True,
                Network.external == True,
            )
        ).order_by(Network.created_at)
    return {"items": [network_to_os_dict(db, n, current) for n in db.scalars(q).all()]}


@app.get("/networks/{ref}")
def show_network(ref: str, current: CurrentUser, db: DB):
    return network_to_os_dict(db, require_by_ref(db, Network, ref, "network"), current)


@app.delete("/networks/{ref}")
def delete_network(ref: str, _: CurrentUser, db: DB):
    network = require_by_ref(db, Network, ref, "network")
    ovn("--if-exists", "ls-del", network.name)
    db.delete(network)
    db.commit()
    return {"status": "deleted", "id": network.id, "name": network.name}


@app.post("/subnets", status_code=201)
def create_subnet(payload: SubnetIn, current: CurrentUser, db: DB):
    if get_by_ref(db, Subnet, payload.name):
        raise HTTPException(status_code=409, detail="subnet already exists")
    network = require_by_ref(db, Network, payload.network, "network")
    gateway = payload.gateway_ip or payload.gateway
    ip_version = payload.ip_version

    # Validate CIDR version matches requested ip_version
    try:
        cidr_net = ipaddress.ip_network(payload.cidr, strict=False)
        if cidr_net.version != ip_version:
            raise ValueError(f"CIDR {payload.cidr} is IPv{cidr_net.version} but ip_version={ip_version}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not gateway:
        # For IPv6 with SLAAC, gateway is optional
        if ip_version == 6 and payload.ipv6_ra_mode in ("slaac", "dhcpv6-stateless"):
            gateway = None
        else:
            raise HTTPException(status_code=400, detail="gateway_ip or gateway is required")

    try:
        cidr = ipaddress.ip_network(payload.cidr, strict=False)
        if gateway:
            gateway_ip = ipaddress.ip_address(gateway)
            if gateway_ip not in cidr:
                raise ValueError(f"gateway {gateway} is outside subnet {payload.cidr}")
        allocation_start = ipaddress.ip_address(payload.allocation_start) if payload.allocation_start else None
        allocation_end = ipaddress.ip_address(payload.allocation_end) if payload.allocation_end else None
        if allocation_start and allocation_start not in cidr:
            raise ValueError("allocation_start is outside subnet")
        if allocation_end and allocation_end not in cidr:
            raise ValueError("allocation_end is outside subnet")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    dns = ",".join(payload.dns_nameservers)
    dhcp_id = None

    if payload.enable_dhcp:
        if ip_version == 4:
            dhcp_id = ovn(*dhcp_options_args(payload.cidr, gateway, payload.dns_nameservers)) if gateway else None
        elif ip_version == 6:
            # IPv6: set up DHCPv6 options or RA mode in OVN
            dhcp_id = _ensure_dhcpv6_options(
                payload.cidr, gateway, payload.ipv6_ra_mode, payload.ipv6_address_mode
            )

    subnet = Subnet(
        name=payload.name,
        network_id=network.id,
        cidr=payload.cidr,
        gateway=gateway or "",
        gateway_ip=gateway,
        allocation_start=str(allocation_start) if allocation_start else None,
        allocation_end=str(allocation_end) if allocation_end else None,
        dns_nameservers=dns or None,
        dhcp_options_id=dhcp_id,
        enable_dhcp=payload.enable_dhcp,
        ovn_dhcp_options_uuid=dhcp_id,
        ip_version=ip_version,
        ipv6_ra_mode=payload.ipv6_ra_mode,
        ipv6_address_mode=payload.ipv6_address_mode,
    )
    db.add(subnet)
    db.commit()
    db.refresh(subnet)
    return obj_to_dict(subnet)


@app.get("/subnets")
def list_subnets(current: CurrentUser, db: DB):
    return {"items": [subnet_to_os_dict(db, s, current) for s in db.scalars(select(Subnet).order_by(Subnet.created_at)).all()]}


@app.get("/subnets/{ref}")
def show_subnet(ref: str, current: CurrentUser, db: DB):
    return subnet_to_os_dict(db, require_by_ref(db, Subnet, ref, "subnet"), current)


@app.delete("/subnets/{ref}")
def delete_subnet(ref: str, _: CurrentUser, db: DB):
    subnet = require_by_ref(db, Subnet, ref, "subnet")
    if subnet.ovn_dhcp_options_uuid or subnet.dhcp_options_id:
        ovn("--if-exists", "destroy", "DHCP_Options", subnet.ovn_dhcp_options_uuid or subnet.dhcp_options_id, check=False)
    db.delete(subnet)
    db.commit()
    return {"status": "deleted", "id": subnet.id, "name": subnet.name}


@app.post("/routers", status_code=201)
def create_router(payload: RouterIn, current: CurrentUser, db: DB):
    if get_by_ref(db, Router, payload.name):
        raise HTTPException(status_code=409, detail="router already exists")
    router = Router(name=payload.name)
    ovn("--may-exist", "lr-add", payload.name)
    ovn("set", "logical_router", payload.name, "external_ids:mini-openstack-lab=true")
    db.add(router)
    db.commit()
    db.refresh(router)
    return obj_to_dict(router)


@app.get("/routers")
def list_routers(current: CurrentUser, db: DB):
    return {"items": [router_to_os_dict(db, r, current) for r in db.scalars(select(Router).order_by(Router.created_at)).all()]}


@app.get("/routers/{ref}")
def show_router(ref: str, current: CurrentUser, db: DB):
    return router_to_os_dict(db, require_by_ref(db, Router, ref, "router"), current)


@app.delete("/routers/{ref}")
def delete_router(ref: str, _: CurrentUser, db: DB):
    router = require_by_ref(db, Router, ref, "router")

    # ── 1. Remove all router interfaces (lrp + lsp) from OVN and DB ──────────
    interfaces = db.scalars(
        select(RouterInterface).where(RouterInterface.router_id == router.id)
    ).all()
    for iface in interfaces:
        ovn("--if-exists", "lsp-del", iface.switch_port_name)
        ovn("--if-exists", "lrp-del", iface.router_port_name)
        db.delete(iface)
    db.flush()  # apply interface deletes before router delete

    # ── 2. Remove the logical router from OVN (also removes its gateway port) ─
    ovn("--if-exists", "lr-del", router.name)

    # ── 3. Delete router record ───────────────────────────────────────────────
    db.delete(router)
    db.commit()
    return {"status": "deleted", "id": router.id, "name": router.name}


@app.post("/routers/{ref}/gateway")
def set_router_gateway(ref: str, payload: RouterGatewayIn, _: CurrentUser, db: DB):
    network_ref = payload.resolved_network
    if not network_ref:
        raise HTTPException(status_code=422, detail="'external_network' field is required")
    router = require_by_ref(db, Router, ref, "router")
    network = require_by_ref(db, Network, network_ref, "network")
    if not network.external:
        raise HTTPException(status_code=400, detail="router gateway requires an external network")
    ensure_router_gateway_ovn(router, network)
    router.external_network_id = network.id
    db.commit()
    db.refresh(router)
    ovn("set", "logical_router", router.name, f"external_ids:external_network={network.name}")
    return {"router": router.name, "external_network": network.name, "status": "ACTIVE"}


@app.post("/routers/{ref}/interfaces", status_code=201)
def add_router_interface(ref: str, payload: RouterInterfaceIn, _: CurrentUser, db: DB):
    router = require_by_ref(db, Router, ref, "router")
    subnet = require_by_ref(db, Subnet, payload.subnet, "subnet")
    network = db.get(Network, subnet.network_id)
    existing = db.scalar(select(RouterInterface).where(RouterInterface.router_id == router.id, RouterInterface.subnet_id == subnet.id))
    if existing:
        raise HTTPException(status_code=409, detail="router interface already exists")
    lrp = f"lrp-{router.name}-{subnet.name}"
    lsp = f"lsp-{router.name}-{subnet.name}"
    port_mac = mac()
    port_ip = subnet_ip_with_prefix(subnet)
    ovn("--may-exist", "lrp-add", router.name, lrp, port_mac, port_ip)
    ovn("set", "logical_router_port", lrp, "external_ids:mini-openstack-lab=true")
    ovn("--may-exist", "lsp-add", network.name, lsp)
    ovn("set", "logical_switch_port", lsp, "external_ids:mini-openstack-lab=true")
    ovn("lsp-set-type", lsp, "router")
    ovn("lsp-set-addresses", lsp, "router")
    ovn("lsp-set-options", lsp, f"router-port={lrp}")
    iface = RouterInterface(router_id=router.id, subnet_id=subnet.id, router_port_name=lrp, switch_port_name=lsp, mac_address=port_mac, ip_address=port_ip)
    db.add(iface)
    db.commit()
    db.refresh(iface)
    # Add OVN static route for Nova metadata (169.254.169.254 → host)
    # Requires PROVIDER_HOST_IP to be set so OVN knows the nexthop.
    # On the host, iptables redirects :80 to metadata-proxy (default :8776).
    if PROVIDER_HOST_IP_RAW:
        ovn("--may-exist", "lr-route-add", router.name,
            f"{METADATA_LINK_LOCAL}/32", PROVIDER_HOST_IP_RAW, check=False)
        print(f"metadata route: {METADATA_LINK_LOCAL}/32 → {PROVIDER_HOST_IP_RAW} on {router.name}", flush=True)
    return obj_to_dict(iface)


@app.delete("/routers/{ref}/interfaces/{subnet_ref}")
def remove_router_interface(ref: str, subnet_ref: str, _: CurrentUser, db: DB):
    router = require_by_ref(db, Router, ref, "router")
    subnet = require_by_ref(db, Subnet, subnet_ref, "subnet")
    iface = db.scalar(select(RouterInterface).where(RouterInterface.router_id == router.id, RouterInterface.subnet_id == subnet.id))
    if not iface:
        raise HTTPException(status_code=404, detail="router interface not found")
    network = db.get(Network, subnet.network_id)
    ovn("--if-exists", "lsp-del", iface.switch_port_name)
    ovn("--if-exists", "lrp-del", iface.router_port_name)
    db.delete(iface)
    db.commit()
    return {"status": "deleted", "router": router.name, "subnet": subnet.name, "network": network.name}


@app.post("/ports", status_code=201)
def create_port(payload: PortIn, current: CurrentUser, db: DB):
    if get_by_ref(db, Port, payload.name):
        raise HTTPException(status_code=409, detail="port already exists")
    network = require_by_ref(db, Network, payload.network, "network")
    subnet = db.scalar(select(Subnet).where(Subnet.network_id == network.id).where(Subnet.ip_version == 4).order_by(Subnet.created_at))
    requested_ip = payload.fixed_ip or payload.ip_address

    # Honor fixed_ips list (dual-stack) if provided
    if payload.fixed_ips:
        for fip_entry in payload.fixed_ips:
            entry_subnet_id = fip_entry.get("subnet_id")
            entry_ip = fip_entry.get("ip_address")
            if entry_subnet_id:
                entry_subnet = db.get(Subnet, entry_subnet_id)
                if entry_subnet and entry_subnet.ip_version == 4:
                    requested_ip = requested_ip or entry_ip
                    subnet = entry_subnet

    fixed_ip = allocate_fixed_ip(db, subnet, requested_ip) if subnet else requested_ip
    port_mac = mac()
    logical_switch_name = network.name
    logical_port_name = payload.name
    security_group_ids = payload.security_group_ids or ["default"]
    groups = [require_by_ref(db, SecurityGroup, group_id, "security-group") for group_id in security_group_ids]

    # Dual-stack: look for an IPv6 subnet on the same network if fixed_ips requests it
    ipv6_address: str | None = None
    if payload.fixed_ips:
        for fip_entry in payload.fixed_ips:
            entry_subnet_id = fip_entry.get("subnet_id")
            entry_ip = fip_entry.get("ip_address")
            if entry_subnet_id:
                entry_subnet = db.get(Subnet, entry_subnet_id)
                if entry_subnet and entry_subnet.ip_version == 6:
                    ipv6_address = allocate_fixed_ip(db, entry_subnet, entry_ip) if not entry_ip else entry_ip

    port = Port(
        name=payload.name,
        network_id=network.id,
        subnet_id=subnet.id if subnet else None,
        mac_address=port_mac,
        ip_address=fixed_ip,
        fixed_ip=fixed_ip,
        ipv6_address=ipv6_address,
        dns_name=payload.dns_name,
        logical_switch_name=logical_switch_name,
        logical_port_name=logical_port_name,
        device_id=payload.device_id,
        device_owner=payload.device_owner,
        security_group_ids=format_id_list([group.id for group in groups]),
        port_security_enabled=payload.port_security_enabled,
    )
    ovn("--may-exist", "lsp-add", network.name, port.name)
    ovn("set", "logical_switch_port", port.name, "external_ids:mini-openstack-lab=true")
    if payload.device_id:
        ovn("set", "logical_switch_port", port.name, f"external_ids:device_id={payload.device_id}")
    if payload.device_owner:
        ovn("set", "logical_switch_port", port.name, f"external_ids:device_owner={payload.device_owner}")

    # Build OVN address string — include both IPv4 and IPv6 if dual-stack
    addr_parts = [port_mac]
    if fixed_ip:
        addr_parts.append(fixed_ip)
    if ipv6_address:
        addr_parts.append(ipv6_address)
    ovn("lsp-set-addresses", port.name, " ".join(addr_parts))

    if subnet and subnet.dhcp_options_id:
        ovn("lsp-set-dhcpv4-options", port.name, subnet.ovn_dhcp_options_uuid or subnet.dhcp_options_id)

    # Register DNS entry in OVN NB DB if dns_name provided
    # This enables VM-to-VM hostname resolution within the network.
    # Future: a Designate-like service can read these records via the API.
    if payload.dns_name and fixed_ip:
        _ovn_register_dns(network.name, payload.dns_name, fixed_ip)

    db.add(port)
    db.commit()
    db.refresh(port)
    apply_security_groups(db, port)
    _publish_net_event("port.create.end", {
        "port_id":    port.id,
        "name":       port.name,
        "network_id": port.network_id,
        "fixed_ip":   port.fixed_ip,
        "device_id":  port.device_id,
    })
    return port_to_dict(db, port)


@app.get("/ports")
def list_ports(_: CurrentUser, db: DB):
    return {"items": [port_to_dict(db, p) for p in db.scalars(select(Port).order_by(Port.created_at)).all()]}


@app.get("/ports/{ref}")
def show_port(ref: str, _: CurrentUser, db: DB):
    return port_to_dict(db, require_by_ref(db, Port, ref, "port"))


@app.post("/ports/{ref}/security-groups")
def set_port_security_groups(ref: str, payload: PortSecurityGroupsIn, _: CurrentUser, db: DB):
    port = require_by_ref(db, Port, ref, "port")
    groups = [require_by_ref(db, SecurityGroup, item, "security-group") for item in payload.security_group_ids]
    port.security_group_ids = format_id_list([group.id for group in groups])
    db.commit()
    db.refresh(port)
    apply_security_groups(db, port)
    return port_to_dict(db, port)


@app.post("/ports/{ref}/security-groups/clear")
def clear_port_security_groups(ref: str, _: CurrentUser, db: DB):
    port = require_by_ref(db, Port, ref, "port")
    port.security_group_ids = None
    db.commit()
    db.refresh(port)
    apply_security_groups(db, port)
    return port_to_dict(db, port)


@app.post("/ports/{ref}/security/enable")
def enable_port_security(ref: str, db: DB):
    port = require_by_ref(db, Port, ref, "port")
    port.port_security_enabled = True
    if not parse_id_list(port.security_group_ids):
        default_group = get_by_ref(db, SecurityGroup, "default")
        if default_group:
            port.security_group_ids = format_id_list([default_group.id])
    db.commit()
    db.refresh(port)
    apply_security_groups(db, port)
    return port_to_dict(db, port)


@app.post("/ports/{ref}/security/disable")
def disable_port_security(ref: str, db: DB):
    port = require_by_ref(db, Port, ref, "port")
    port.port_security_enabled = False
    db.commit()
    db.refresh(port)
    apply_security_groups(db, port)
    return port_to_dict(db, port)


@app.get("/ports/{ref}/ovn")
def port_ovn_show(ref: str, db: DB):
    port = require_by_ref(db, Port, ref, "port")
    groups = []
    for group_id in parse_id_list(port.security_group_ids):
        group = db.get(SecurityGroup, group_id)
        if group:
            groups.append({"id": group.id, "name": group.name, "port_group": port_group_name(group.id)})
    return {
        "port": port_to_dict(db, port),
        "logical_port_name": port.logical_port_name,
        "port_security_enabled": port.port_security_enabled,
        "security_groups": groups,
    }


@app.delete("/ports/{ref}")
def delete_port(ref: str, _: CurrentUser, db: DB):
    port = require_by_ref(db, Port, ref, "port")
    port.security_group_ids = None
    apply_security_groups(db, port)
    ovn("--if-exists", "lsp-del", port.name)
    db.delete(port)
    db.commit()
    return {"status": "deleted", "id": port.id, "name": port.name}


# ── Internal port management (service-to-service, no auth) ────────────────────
# Mirrors block-store's /internal/volumes pattern: compute-worker calls these
# during instance deletion, where it has no user auth token to present.

def _disassociate_floating_ips_for_port(db: Session, port: Port) -> list[str]:
    """Reset any floating IPs bound to this port: remove the OVN NAT rule and
    clear the association so the FIP returns to DOWN/unallocated-binding state.

    Called before a port is deleted (instance teardown / purge) — otherwise the
    FIP row keeps pointing at a port that no longer exists.
    Returns the list of released floating IP addresses."""
    fips = db.scalars(
        select(FloatingIP).where(
            or_(FloatingIP.port_id == port.id,
                FloatingIP.fixed_port_id == port.id)
        )
    ).all()
    released = []
    for fip in fips:
        router = db.get(Router, fip.router_id) if fip.router_id else None
        if router and fip.floating_ip:
            ovn("--if-exists", "lr-nat-del", router.name, "dnat_and_snat", fip.floating_ip, check=False)
        fip.fixed_port_id = None
        fip.port_id = None
        fip.fixed_ip_address = None
        fip.server_id = None
        fip.router_id = None
        fip.status = "DOWN"
        released.append(fip.floating_ip or fip.floating_ip_address or fip.id)
    if fips:
        db.commit()
    return released


def _delete_port_record(db: Session, port: Port) -> dict:
    _disassociate_floating_ips_for_port(db, port)
    port.security_group_ids = None
    apply_security_groups(db, port)
    ovn("--if-exists", "lsp-del", port.name)
    db.delete(port)
    db.commit()
    return {"status": "deleted", "id": port.id, "name": port.name}


@app.delete("/internal/ports/{ref}", status_code=200)
def delete_port_internal(ref: str, db: DB):
    """Service-to-service port deletion — used by compute-worker on instance teardown."""
    port = require_by_ref(db, Port, ref, "port")
    return _delete_port_record(db, port)


@app.post("/internal/ports/purge-orphaned")
def purge_orphaned_ports(db: DB):
    """Delete compute-owned ports whose server no longer exists, and release
    floating IPs pointing at ports that are already gone.

    Historical safety net: before /internal/ports existed, compute-worker's
    port cleanup failed silently (401 against the authed endpoint), so ports
    of deleted instances accumulated; floating IPs bound to those ports kept
    referencing dead fixed IPs. A port is orphaned when its device_owner
    starts with 'compute:' and its device_id no longer matches any live
    compute_servers row (all services share the same PostgreSQL DB).
    """
    compute_ports = db.scalars(
        select(Port).where(Port.device_owner.like("compute:%"), Port.device_id.is_not(None))
    ).all()
    deleted = []
    for port in compute_ports:
        live = db.execute(
            text("SELECT 1 FROM compute_servers WHERE id = :sid AND status != 'DELETED'"),
            {"sid": port.device_id},
        ).first()
        if not live:
            try:
                _delete_port_record(db, port)
                deleted.append({"id": port.id, "name": port.name, "device_id": port.device_id})
            except Exception:
                continue

    # Floating IPs whose port_id no longer resolves to any port row —
    # leftovers from deletions that happened before the port-delete hook
    # existed. Release them the same way disassociate does.
    stale_fips = db.scalars(
        select(FloatingIP).where(FloatingIP.port_id.is_not(None))
    ).all()
    live_port_ids = {p.id for p in db.scalars(select(Port)).all()}
    released_fips = []
    for fip in stale_fips:
        if fip.port_id in live_port_ids:
            continue
        router = db.get(Router, fip.router_id) if fip.router_id else None
        if router and fip.floating_ip:
            ovn("--if-exists", "lr-nat-del", router.name, "dnat_and_snat", fip.floating_ip, check=False)
        fip.fixed_port_id = None
        fip.port_id = None
        fip.fixed_ip_address = None
        fip.server_id = None
        fip.router_id = None
        fip.status = "DOWN"
        released_fips.append(fip.floating_ip or fip.floating_ip_address or fip.id)
    if released_fips:
        db.commit()

    return {"purged": len(deleted), "ports": deleted, "released_floating_ips": released_fips}


@app.post("/floating-ips", status_code=201)
def create_floating_ip(payload: FloatingIPIn, current: CurrentUser, db: DB):
    if payload.name and get_by_ref(db, FloatingIP, payload.name):
        raise HTTPException(status_code=409, detail="floating-ip already exists")
    ext_net_ref = payload.external_network or payload.network
    external_network = require_by_ref(db, Network, ext_net_ref, "network") if ext_net_ref else None
    if external_network and not external_network.external:
        raise HTTPException(status_code=400, detail="floating IP network must be external")
    floating_ip = payload.floating_ip or (allocate_floating_ip(db, external_network) if external_network else None)
    if not floating_ip:
        raise HTTPException(status_code=400, detail="floating_ip or external_network is required")
    name = payload.name or f"fip-{floating_ip.replace('.', '-')}"
    if get_by_ref(db, FloatingIP, name):
        if payload.name:
            raise HTTPException(status_code=409, detail="floating-ip already exists")
        name = f"fip-{floating_ip.replace('.', '-')}-{uuid.uuid4().hex[:4]}"
    port = require_by_ref(db, Port, payload.fixed_port, "port") if payload.fixed_port else None
    router = None
    if port:
        try:
            router = find_router_for_fip(db, None)
            fixed_ip = port.fixed_ip or port.ip_address
            if router and fixed_ip:
                ovn(*nat_rule_args(router.name, floating_ip, fixed_ip))
        except Exception as exc:
            log.warning("Could not setup OVN NAT for new FIP: %s", exc)

    fip = FloatingIP(
        name=name,
        floating_ip=floating_ip,
        floating_ip_address=floating_ip,
        fixed_ip_address=(port.fixed_ip or port.ip_address) if port else None,
        fixed_port_id=port.id if port else None,
        port_id=port.id if port else None,
        server_id=port.device_id if port else None,
        external_network_id=external_network.id if external_network else None,
        router_id=router.id if router else None,
        status="ACTIVE" if port else "DOWN",
    )
    db.add(fip)
    db.commit()
    db.refresh(fip)
    return obj_to_dict(fip)


@app.get("/floating-ips")
def list_floating_ips(current: CurrentUser, db: DB):
    return {"items": [fip_to_os_dict(db, f, current) for f in db.scalars(select(FloatingIP).order_by(FloatingIP.created_at)).all()]}


@app.get("/floating-ips/{ref}")
def show_floating_ip(ref: str, current: CurrentUser, db: DB):
    return fip_to_os_dict(db, require_floating_ip(db, ref), current)


@app.post("/floating-ips/{ref}/associate")
def associate_floating_ip(ref: str, payload: FloatingIPAssociateIn, current: CurrentUser, db: DB):
    fip = require_floating_ip(db, ref)
    port = resolve_association_port(db, payload)
    router = find_router_for_fip(db, payload.router)
    fixed_ip = port.fixed_ip or port.ip_address
    if not fixed_ip:
        raise HTTPException(status_code=400, detail="port has no fixed IP")
    ovn(*nat_rule_args(router.name, fip.floating_ip, fixed_ip))
    fip.fixed_port_id = port.id
    fip.port_id = port.id
    fip.fixed_ip_address = fixed_ip
    fip.server_id = port.device_id
    fip.router_id = router.id
    fip.status = "ACTIVE"
    db.commit()
    db.refresh(fip)
    return fip_to_os_dict(db, fip, current)


@app.post("/floating-ips/{ref}/disassociate")
def disassociate_floating_ip(ref: str, _: CurrentUser, db: DB):
    fip = require_floating_ip(db, ref)
    router = db.get(Router, fip.router_id) if fip.router_id else None
    if router and fip.floating_ip:
        ovn("--if-exists", "lr-nat-del", router.name, "dnat_and_snat", fip.floating_ip, check=False)
    fip.fixed_port_id = None
    fip.port_id = None
    fip.fixed_ip_address = None
    fip.server_id = None
    fip.router_id = None
    fip.status = "DOWN"
    db.commit()
    db.refresh(fip)
    return fip_to_os_dict(db, fip)


@app.delete("/floating-ips/{ref}")
def delete_floating_ip(ref: str, _: CurrentUser, db: DB):
    fip = require_floating_ip(db, ref)
    if fip.router_id and fip.floating_ip:
        router = db.get(Router, fip.router_id)
        if router:
            ovn("--if-exists", "lr-nat-del", router.name, "dnat_and_snat", fip.floating_ip, check=False)
    db.delete(fip)
    db.commit()
    return {"status": "deleted", "id": fip.id, "name": fip.name}


@app.post("/security-groups", status_code=201)
def create_security_group(payload: SecurityGroupIn, current: CurrentUser, db: DB):
    if get_by_ref(db, SecurityGroup, payload.name):
        raise HTTPException(status_code=409, detail="security-group already exists")
    group = SecurityGroup(name=payload.name, description=payload.description, project_id=current.get("project_id"))
    db.add(group)
    db.commit()
    db.refresh(group)
    apply_security_group_acls(db, group)
    return obj_to_dict(group)


@app.get("/security-groups")
def list_security_groups(current: CurrentUser, db: DB):
    return {"items": [sg_to_os_dict(db, group, current, include_rules=False) for group in db.scalars(select(SecurityGroup).order_by(SecurityGroup.created_at)).all()]}


@app.get("/security-groups/{ref}")
def show_security_group(ref: str, current: CurrentUser, db: DB):
    group = require_by_ref(db, SecurityGroup, ref, "security-group")
    return sg_to_os_dict(db, group, current, include_rules=True)


@app.delete("/security-groups/{ref}")
def delete_security_group(ref: str, _: CurrentUser, db: DB):
    group = require_by_ref(db, SecurityGroup, ref, "security-group")
    in_use = [p.name for p in db.scalars(select(Port).where(Port.security_group_ids.is_not(None))).all() if group.id in parse_id_list(p.security_group_ids)]
    if in_use:
        raise HTTPException(status_code=409, detail=f"security-group is still attached to ports: {', '.join(in_use)}")
    for rule in db.scalars(select(SecurityGroupRule).where(SecurityGroupRule.security_group_id == group.id)).all():
        db.delete(rule)
    ovn("--if-exists", "pg-del", port_group_name(group.id), check=False)
    db.delete(group)
    db.commit()
    return {"status": "deleted", "id": group.id, "name": group.name}


@app.post("/security-groups/{ref}/rules", status_code=201)
def create_security_group_rule(ref: str, payload: SecurityGroupRuleIn, _: CurrentUser, db: DB):
    group = require_by_ref(db, SecurityGroup, ref, "security-group")
    if payload.direction not in {"ingress", "egress"}:
        raise HTTPException(status_code=400, detail="direction must be ingress or egress")
    if payload.protocol not in {"tcp", "udp", "icmp", "any"}:
        raise HTTPException(status_code=400, detail="protocol must be tcp, udp, icmp, or any")
    rule = SecurityGroupRule(
        security_group_id=group.id,
        direction=payload.direction,
        ethertype=payload.ethertype,
        protocol=payload.protocol,
        port_min=str(payload.port_min) if payload.port_min is not None else None,
        port_max=str(payload.port_max or payload.port_min) if payload.port_min is not None else None,
        remote_ip_prefix=payload.remote_ip_prefix,
        remote_group_id=payload.remote_group_id,
        action=payload.action,
    )
    db.add(rule)
    group.updated_at = now()
    db.commit()
    db.refresh(rule)
    apply_security_group_acls(db, group)
    return obj_to_dict(rule)


@app.get("/security-groups/{ref}/rules")
def list_security_group_rules(ref: str, _: CurrentUser, db: DB):
    group = require_by_ref(db, SecurityGroup, ref, "security-group")
    return {"items": [obj_to_dict(rule) for rule in db.scalars(select(SecurityGroupRule).where(SecurityGroupRule.security_group_id == group.id).order_by(SecurityGroupRule.created_at)).all()]}


@app.delete("/security-group-rules/{rule_id}")
def delete_security_group_rule(rule_id: str, _: CurrentUser, db: DB):
    rule = require_by_ref(db, SecurityGroupRule, rule_id, "security-group-rule")
    group = db.get(SecurityGroup, rule.security_group_id)
    db.delete(rule)
    if group:
        group.updated_at = now()
    db.commit()
    if group:
        apply_security_group_acls(db, group)
    return {"status": "deleted", "id": rule_id}


@app.get("/security-groups/{ref}/ovn")
def security_group_ovn_show(ref: str, db: DB):
    group = require_by_ref(db, SecurityGroup, ref, "security-group")
    pg = ensure_security_group_port_group(group)
    members = []
    for port in db.scalars(select(Port).where(Port.security_group_ids.is_not(None)).order_by(Port.created_at)).all():
        if group.id in parse_id_list(port.security_group_ids) and port.port_security_enabled:
            members.append(port.logical_port_name or port.name)
    rules = db.scalars(select(SecurityGroupRule).where(SecurityGroupRule.security_group_id == group.id).order_by(SecurityGroupRule.created_at)).all()
    translated = [
        {
            "rule_id": rule.id,
            "direction": "to-lport" if rule.direction == "ingress" else "from-lport",
            "priority": acl_priority(rule),
            "match": acl_match(group.id, rule),
            "action": "allow-related" if rule.action == "allow" else rule.action,
        }
        for rule in rules
    ]
    translated.append({"rule_id": "default-drop-ingress", "direction": "to-lport", "priority": "1001", "match": f"outport == @{pg} && ip4", "action": "drop"})
    return {"security_group": obj_to_dict(group), "port_group": pg, "member_ports": members, "translated_rules": translated}


@app.get("/ovn/show")
def ovn_show():
    return {"output": ovn("show")}


# ── QoS Policies (Neutron QoS Extension) ────────────────────────────────────────────────────────

class QosPolicyIn(BaseModel):
    name: str
    description: str | None = None
    shared: bool = False


class QosBwLimitRuleIn(BaseModel):
    max_kbps: int
    max_burst_kbps: int = 0
    direction: str = "egress"  # egress | ingress


def _qos_to_dict(policy: QosPolicy, rules: list) -> dict:
    return {
        "id": policy.id,
        "name": policy.name,
        "description": policy.description,
        "shared": policy.shared,
        "project_id": policy.project_id,
        "rules": [
            {
                "id": r.id,
                "max_kbps": r.max_kbps,
                "max_burst_kbps": r.max_burst_kbps,
                "direction": r.direction,
                "type": "bandwidth_limit",
            }
            for r in rules
        ],
        "created_at": policy.created_at.isoformat(),
    }


@app.post("/qos/policies", status_code=201)
def create_qos_policy(payload: QosPolicyIn, current: CurrentUser, db: DB):
    if db.scalar(select(QosPolicy).where(QosPolicy.name == payload.name)):
        raise HTTPException(status_code=409, detail="QoS policy already exists")
    policy = QosPolicy(**payload.model_dump(), project_id=current.get("project_id"))
    db.add(policy)
    db.commit()
    db.refresh(policy)
    return _qos_to_dict(policy, [])


@app.get("/qos/policies")
def list_qos_policies(current: CurrentUser, db: DB):
    q = select(QosPolicy)
    if current.get("role") != "admin":
        q = q.where(
            (QosPolicy.shared == True) | (QosPolicy.project_id == current.get("project_id"))
        )
    policies = db.scalars(q).all()
    result = []
    for p in policies:
        rules = db.scalars(select(QosBandwidthLimitRule).where(QosBandwidthLimitRule.qos_policy_id == p.id)).all()
        result.append(_qos_to_dict(p, rules))
    return {"policies": result}


@app.get("/qos/policies/{policy_id}")
def show_qos_policy(policy_id: str, _: CurrentUser, db: DB):
    policy = db.get(QosPolicy, policy_id) or db.scalar(select(QosPolicy).where(QosPolicy.name == policy_id))
    if not policy:
        raise HTTPException(status_code=404, detail="QoS policy not found")
    rules = db.scalars(select(QosBandwidthLimitRule).where(QosBandwidthLimitRule.qos_policy_id == policy.id)).all()
    return _qos_to_dict(policy, rules)


@app.delete("/qos/policies/{policy_id}", status_code=204)
def delete_qos_policy(policy_id: str, _: AdminUser, db: DB):
    policy = db.get(QosPolicy, policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail="QoS policy not found")
    db.delete(policy)
    db.commit()


@app.post("/qos/policies/{policy_id}/bandwidth_limit_rules", status_code=201)
def create_bw_limit_rule(policy_id: str, payload: QosBwLimitRuleIn, _: CurrentUser, db: DB):
    policy = db.get(QosPolicy, policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail="QoS policy not found")
    rule = QosBandwidthLimitRule(
        qos_policy_id=policy_id,
        max_kbps=payload.max_kbps,
        max_burst_kbps=payload.max_burst_kbps,
        direction=payload.direction,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    # Apply OVN QoS on affected ports
    _apply_qos_to_policy_ports(policy_id, rule, db)
    return {
        "id": rule.id, "max_kbps": rule.max_kbps,
        "max_burst_kbps": rule.max_burst_kbps, "direction": rule.direction,
    }


@app.delete("/qos/policies/{policy_id}/bandwidth_limit_rules/{rule_id}", status_code=204)
def delete_bw_limit_rule(policy_id: str, rule_id: str, _: CurrentUser, db: DB):
    rule = db.get(QosBandwidthLimitRule, rule_id)
    if not rule or rule.qos_policy_id != policy_id:
        raise HTTPException(status_code=404, detail="rule not found")
    db.delete(rule)
    db.commit()


def _apply_qos_to_policy_ports(policy_id: str, rule: QosBandwidthLimitRule, db) -> None:
    """Apply OVN QoS meter to all ports associated with this policy."""
    ports = db.scalars(select(Port).where(Port.qos_policy_id == policy_id)).all()
    for port in ports:
        if port.logical_switch_name and port.logical_port_name:
            _apply_ovn_qos(port.logical_switch_name, port.logical_port_name, rule)


def _apply_ovn_qos(ls_name: str, lp_name: str, rule: QosBandwidthLimitRule) -> None:
    """Set OVN QoS bandwidth limit on a logical switch port."""
    # Convert kbps to bps for OVN
    rate_bps = rule.max_kbps * 1000
    burst_bps = rule.max_burst_kbps * 1000 if rule.max_burst_kbps else rate_bps
    direction = "from-lport" if rule.direction == "egress" else "to-lport"
    try:
        # Remove existing QoS rule for this port
        ovn(f'--where direction={direction} --data-bare --no-heading --columns _uuid find QoS '
            f'match="inport == \'{lp_name}\'"')
        # Add new bandwidth rule
        ovn(
            f'set Logical_Switch {ls_name} '
            f'qos_rules=[{{"direction": "{direction}", '
            f'"match": "inport == \'{lp_name}\'", '
            f'"bandwidth": {{"rate": {rate_bps}, "burst": {burst_bps}}}, '
            f'"priority": 100}}]'
        )
    except Exception as exc:
        import logging
        logging.getLogger("networking-api").warning("OVN QoS apply failed for port %s: %s", lp_name, exc)


# ── Subnet Pools ────────────────────────────────────────────────────────────────────────────

class SubnetPoolIn(BaseModel):
    name: str
    prefixes: list[str]         # e.g. ["10.0.0.0/8", "192.168.0.0/16"]
    default_prefixlen: int = 24
    min_prefixlen: int = 8
    max_prefixlen: int = 32
    shared: bool = False


def _pool_to_dict(pool: SubnetPool) -> dict:
    return {
        "id": pool.id,
        "name": pool.name,
        "prefixes": pool.prefixes.split(",") if pool.prefixes else [],
        "default_prefixlen": pool.default_prefixlen,
        "min_prefixlen": pool.min_prefixlen,
        "max_prefixlen": pool.max_prefixlen,
        "shared": pool.shared,
        "project_id": pool.project_id,
        "created_at": pool.created_at.isoformat(),
    }


@app.post("/subnetpools", status_code=201)
def create_subnet_pool(payload: SubnetPoolIn, current: CurrentUser, db: DB):
    if db.scalar(select(SubnetPool).where(SubnetPool.name == payload.name)):
        raise HTTPException(status_code=409, detail="subnet pool already exists")
    pool = SubnetPool(
        name=payload.name,
        prefixes=",".join(payload.prefixes),
        default_prefixlen=payload.default_prefixlen,
        min_prefixlen=payload.min_prefixlen,
        max_prefixlen=payload.max_prefixlen,
        shared=payload.shared,
        project_id=current.get("project_id"),
    )
    db.add(pool)
    db.commit()
    db.refresh(pool)
    return _pool_to_dict(pool)


@app.get("/subnetpools")
def list_subnet_pools(current: CurrentUser, db: DB):
    q = select(SubnetPool)
    if current.get("role") != "admin":
        q = q.where(
            (SubnetPool.shared == True) | (SubnetPool.project_id == current.get("project_id"))
        )
    return {"subnet_pools": [_pool_to_dict(p) for p in db.scalars(q).all()]}


@app.get("/subnetpools/{pool_id}")
def show_subnet_pool(pool_id: str, _: CurrentUser, db: DB):
    pool = db.get(SubnetPool, pool_id) or db.scalar(select(SubnetPool).where(SubnetPool.name == pool_id))
    if not pool:
        raise HTTPException(status_code=404, detail="subnet pool not found")
    return _pool_to_dict(pool)


@app.delete("/subnetpools/{pool_id}", status_code=204)
def delete_subnet_pool(pool_id: str, _: AdminUser, db: DB):
    pool = db.get(SubnetPool, pool_id)
    if not pool:
        raise HTTPException(status_code=404, detail="subnet pool not found")
    db.delete(pool)
    db.commit()


# ── Port Forwarding (DNAT per-port) ─────────────────────────────────────────────────────────


class PortForwardingIn(BaseModel):
    protocol: str = "tcp"                # tcp | udp
    external_port: int
    internal_port: int
    internal_ip_address: str
    description: str | None = None


def _pf_to_dict(pf: PortForwarding) -> dict:
    return {
        "id": pf.id,
        "floatingip_id": pf.floatingip_id,
        "protocol": pf.protocol,
        "external_port": pf.external_port,
        "internal_port": pf.internal_port,
        "internal_ip_address": pf.internal_ip_address,
        "description": pf.description,
        "created_at": pf.created_at.isoformat(),
    }


def _apply_pf_ovn_rule(fip: FloatingIP, pf: PortForwarding, delete: bool = False) -> None:
    """Apply or remove an OVN DNAT rule for a port forwarding entry."""
    if not fip.router_id or not fip.floating_ip:
        return
    from sqlalchemy.orm import Session as _Sess
    # We need a router name — look it up
    db_temp = SessionLocal()
    try:
        router = db_temp.get(Router, fip.router_id)
        if not router:
            return
        external_ip = fip.floating_ip_address or fip.floating_ip
        if delete:
            ovn(
                "--if-exists", "lr-nat-del", router.name, "dnat",
                f"{external_ip}:{pf.external_port}",
                check=False,
            )
        else:
            ovn(
                "--may-exist", "lr-nat-add", router.name, "dnat",
                f"{external_ip}:{pf.external_port}",
                f"{pf.internal_ip_address}:{pf.internal_port}",
                f"--protocol={pf.protocol}",
                check=False,
            )
    finally:
        db_temp.close()


@app.post("/floatingips/{fip_ref}/port_forwardings", status_code=201)
def create_port_forwarding(fip_ref: str, payload: PortForwardingIn, _: CurrentUser, db: DB):
    """Create a DNAT port forwarding rule on a floating IP."""
    fip = require_floating_ip(db, fip_ref)
    if payload.protocol not in {"tcp", "udp"}:
        raise HTTPException(status_code=400, detail="protocol must be tcp or udp")
    if not (1 <= payload.external_port <= 65535) or not (1 <= payload.internal_port <= 65535):
        raise HTTPException(status_code=400, detail="port numbers must be 1-65535")
    # Check for conflict on same external_port
    existing = db.scalar(
        select(PortForwarding).where(
            PortForwarding.floatingip_id == fip.id,
            PortForwarding.external_port == payload.external_port,
            PortForwarding.protocol == payload.protocol,
        )
    )
    if existing:
        raise HTTPException(status_code=409, detail=f"port forwarding already exists for {payload.protocol}:{payload.external_port}")
    pf = PortForwarding(
        floatingip_id=fip.id,
        protocol=payload.protocol,
        external_port=payload.external_port,
        internal_port=payload.internal_port,
        internal_ip_address=payload.internal_ip_address,
        description=payload.description,
    )
    db.add(pf)
    db.commit()
    db.refresh(pf)
    # Apply OVN DNAT rule
    _apply_pf_ovn_rule(fip, pf, delete=False)
    return _pf_to_dict(pf)


@app.get("/floatingips/{fip_ref}/port_forwardings")
def list_port_forwardings(fip_ref: str, _: CurrentUser, db: DB):
    """List all port forwarding rules for a floating IP."""
    fip = require_floating_ip(db, fip_ref)
    pfs = db.scalars(
        select(PortForwarding).where(PortForwarding.floatingip_id == fip.id).order_by(PortForwarding.created_at)
    ).all()
    return {"port_forwardings": [_pf_to_dict(pf) for pf in pfs]}


@app.get("/floatingips/{fip_ref}/port_forwardings/{pf_id}")
def show_port_forwarding(fip_ref: str, pf_id: str, _: CurrentUser, db: DB):
    """Get a specific port forwarding rule."""
    fip = require_floating_ip(db, fip_ref)
    pf = db.get(PortForwarding, pf_id)
    if not pf or pf.floatingip_id != fip.id:
        raise HTTPException(status_code=404, detail="port forwarding not found")
    return _pf_to_dict(pf)


@app.delete("/floatingips/{fip_ref}/port_forwardings/{pf_id}", status_code=204)
def delete_port_forwarding(fip_ref: str, pf_id: str, _: CurrentUser, db: DB):
    """Delete a port forwarding rule and remove OVN DNAT entry."""
    fip = require_floating_ip(db, fip_ref)
    pf = db.get(PortForwarding, pf_id)
    if not pf or pf.floatingip_id != fip.id:
        raise HTTPException(status_code=404, detail="port forwarding not found")
    _apply_pf_ovn_rule(fip, pf, delete=True)
    db.delete(pf)
    db.commit()


# ── API Version Discovery ────────────────────────────────────────────────────────

@app.get("/v2.0")
def neutron_v2_root():
    """Neutron v2.0 API version discovery."""
    return {
        "version": {
            "id": "v2.0",
            "status": "CURRENT",
            "updated": "2013-07-23T11:33:21Z",
            "links": [{"rel": "self", "href": "/v2.0"}],
        }
    }


@app.get("/")
def networking_root():
    """Networking API root — version discovery."""
    return {
        "versions": [{
            "id": "v2.0",
            "status": "CURRENT",
            "links": [{"rel": "self", "href": "/v2.0"}],
        }]
    }


# ── Trunk Ports ──────────────────────────────────────────────────────────────
# Trunk ports allow a parent port to carry multiple VLANs via sub-ports.
# Each sub-port is a regular port with a VLAN tag set in OVN.
# Reference: https://docs.openstack.org/neutron/latest/admin/config-trunking.html

def _trunk_to_dict(db: Session, trunk: Trunk) -> dict:
    sub_ports = db.scalars(
        select(TrunkSubPort).where(TrunkSubPort.trunk_id == trunk.id)
    ).all()
    return {
        "id":          trunk.id,
        "name":        trunk.name,
        "description": trunk.description,
        "port_id":     trunk.port_id,
        "status":      trunk.status,
        "project_id":  trunk.project_id,
        "created_at":  _fmt_ts(trunk.created_at),
        "sub_ports": [
            {
                "port_id":          sp.port_id,
                "segmentation_type": sp.segmentation_type,
                "segmentation_id":   sp.segmentation_id,
            }
            for sp in sub_ports
        ],
    }


@app.post("/trunks", status_code=201)
def create_trunk(payload: TrunkIn, current: CurrentUser, db: DB):
    """Create a trunk port. The parent port must already exist."""
    parent_port = require_by_ref(db, Port, payload.port_id, "port")
    if db.scalar(select(Trunk).where(Trunk.name == payload.name)):
        raise HTTPException(status_code=409, detail="trunk already exists")
    if db.scalar(select(Trunk).where(Trunk.port_id == parent_port.id)):
        raise HTTPException(status_code=409, detail="port is already a trunk parent")
    trunk = Trunk(
        name=payload.name,
        description=payload.description,
        port_id=parent_port.id,
        project_id=payload.project_id or current.get("project_id"),
    )
    db.add(trunk)
    db.commit()
    db.refresh(trunk)
    return _trunk_to_dict(db, trunk)


@app.get("/trunks")
def list_trunks(current: CurrentUser, db: DB):
    """List all trunk ports."""
    trunks = db.scalars(select(Trunk).order_by(Trunk.created_at)).all()
    return {"trunks": [_trunk_to_dict(db, t) for t in trunks]}


@app.get("/trunks/{trunk_ref}")
def show_trunk(trunk_ref: str, _: CurrentUser, db: DB):
    """Get details of a specific trunk."""
    trunk = db.get(Trunk, trunk_ref) or db.scalar(select(Trunk).where(Trunk.name == trunk_ref))
    if not trunk:
        raise HTTPException(status_code=404, detail="trunk not found")
    return _trunk_to_dict(db, trunk)


@app.delete("/trunks/{trunk_ref}", status_code=204)
def delete_trunk(trunk_ref: str, _: CurrentUser, db: DB):
    """Delete a trunk and all its sub-port associations."""
    trunk = db.get(Trunk, trunk_ref) or db.scalar(select(Trunk).where(Trunk.name == trunk_ref))
    if not trunk:
        raise HTTPException(status_code=404, detail="trunk not found")
    # Ensure no active sub-ports remain
    sub_count = db.scalar(select(TrunkSubPort).where(TrunkSubPort.trunk_id == trunk.id))
    if sub_count:
        raise HTTPException(status_code=409, detail="remove all sub_ports before deleting the trunk")
    db.delete(trunk)
    db.commit()


@app.put("/trunks/{trunk_ref}/add_subports")
def add_subports(trunk_ref: str, payload: TrunkSubPortsPayload, _: CurrentUser, db: DB):
    """
    Add sub-ports to a trunk.
    Each sub-port is an existing port that will be VLAN-tagged in OVN.
    """
    trunk = db.get(Trunk, trunk_ref) or db.scalar(select(Trunk).where(Trunk.name == trunk_ref))
    if not trunk:
        raise HTTPException(status_code=404, detail="trunk not found")

    added = []
    for sp_in in payload.sub_ports:
        # Validate sub-port exists
        sub_port = require_by_ref(db, Port, sp_in.port_id, "port")
        # Check for duplicate segmentation_id in this trunk
        existing_seg = db.scalar(
            select(TrunkSubPort).where(
                TrunkSubPort.trunk_id == trunk.id,
                TrunkSubPort.segmentation_id == sp_in.segmentation_id,
            )
        )
        if existing_seg:
            raise HTTPException(
                status_code=409,
                detail=f"segmentation_id {sp_in.segmentation_id} already in use on this trunk"
            )
        # Validate VLAN ID range
        if not (1 <= sp_in.segmentation_id <= 4094):
            raise HTTPException(status_code=400, detail="segmentation_id must be between 1 and 4094")

        sp = TrunkSubPort(
            trunk_id=trunk.id,
            port_id=sub_port.id,
            segmentation_type=sp_in.segmentation_type,
            segmentation_id=sp_in.segmentation_id,
        )
        db.add(sp)

        # Apply VLAN tag in OVN: set tag on the logical switch port
        # OVN uses 'tag' column on the Logical_Switch_Port for VLAN tagging
        ovn("set", "Logical_Switch_Port", sub_port.name,
            f"tag={sp_in.segmentation_id}", check=False)

        added.append({
            "port_id": sub_port.id,
            "segmentation_type": sp_in.segmentation_type,
            "segmentation_id": sp_in.segmentation_id,
        })

    db.commit()
    return {"sub_ports": added}


@app.put("/trunks/{trunk_ref}/remove_subports")
def remove_subports(trunk_ref: str, payload: TrunkSubPortsPayload, _: CurrentUser, db: DB):
    """Remove sub-ports from a trunk and clear their VLAN tags in OVN."""
    trunk = db.get(Trunk, trunk_ref) or db.scalar(select(Trunk).where(Trunk.name == trunk_ref))
    if not trunk:
        raise HTTPException(status_code=404, detail="trunk not found")

    removed = []
    for sp_in in payload.sub_ports:
        sub_port = db.get(Port, sp_in.port_id)
        if not sub_port:
            continue
        sp_record = db.scalar(
            select(TrunkSubPort).where(
                TrunkSubPort.trunk_id == trunk.id,
                TrunkSubPort.port_id == sub_port.id,
            )
        )
        if sp_record:
            db.delete(sp_record)
            # Clear OVN VLAN tag
            ovn("clear", "Logical_Switch_Port", sub_port.name, "tag", check=False)
            removed.append({"port_id": sub_port.id})

    db.commit()
    return {"sub_ports": removed}

