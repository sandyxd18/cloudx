import ipaddress
import os
import secrets
import subprocess
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, DateTime, ForeignKey, String, create_engine, inspect, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

try:
    from app.auth_middleware import CurrentUser, AdminUser, OptionalAuth
except ImportError:
    from auth_middleware import CurrentUser, AdminUser, OptionalAuth


DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://mini:mini@postgres:5432/mini_openstack")
OVN_NB_DB = os.getenv("OVN_NB_DB", "tcp:ovn-nb-db:6641")
PROVIDER_INTERFACE = os.getenv("PROVIDER_INTERFACE", "enp0s8")
PROVIDER_BRIDGE = os.getenv("PROVIDER_BRIDGE", "br-ex")
PROVIDER_NETWORK_NAME = os.getenv("PROVIDER_NETWORK_NAME", "public-net")
PROVIDER_SUBNET_NAME = os.getenv("PROVIDER_SUBNET_NAME", "public-subnet")
PROVIDER_CIDR = os.getenv("PROVIDER_CIDR", "192.168.56.0/24")
PROVIDER_GATEWAY = os.getenv("PROVIDER_GATEWAY", "192.168.56.1")
PROVIDER_ALLOCATION_START = os.getenv("PROVIDER_ALLOCATION_START", "192.168.56.100")
PROVIDER_ALLOCATION_END = os.getenv("PROVIDER_ALLOCATION_END", "192.168.56.200")
PROVIDER_DNS = [item.strip() for item in os.getenv("PROVIDER_DNS", "8.8.8.8").split(",") if item.strip()]
PROVIDER_ENABLE_DHCP = os.getenv("PROVIDER_ENABLE_DHCP", "false").lower() == "true"
PROVIDER_PHYSNET = os.getenv("PROVIDER_PHYSNET", "physnet1")
# Strip CIDR notation if present (e.g. "192.168.10.102/24" → "192.168.10.102")
PROVIDER_HOST_IP_RAW = os.getenv("PROVIDER_HOST_IP", "").split("/")[0].strip()
METADATA_LINK_LOCAL = "169.254.169.254"
# Set to "true" to auto-create provider network on startup (legacy behaviour).
# Default is "false": user creates public-net manually via the CLI / lab-setup.sh.
PROVIDER_NETWORK_AUTO_CREATE = os.getenv("PROVIDER_NETWORK_AUTO_CREATE", "false").lower() == "true"
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
    provider: Mapped[bool] = mapped_column(Boolean, default=False)
    network_type: Mapped[str | None] = mapped_column(String, nullable=True)
    physical_network: Mapped[str | None] = mapped_column(String, nullable=True)
    bridge_mapping: Mapped[str | None] = mapped_column(String, nullable=True)
    provider_bridge: Mapped[str | None] = mapped_column(String, nullable=True)
    provider_cidr: Mapped[str | None] = mapped_column(String, nullable=True)
    gateway_ip: Mapped[str | None] = mapped_column(String, nullable=True)
    allocation_start: Mapped[str | None] = mapped_column(String, nullable=True)
    allocation_end: Mapped[str | None] = mapped_column(String, nullable=True)
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
    ip_address: Mapped[str | None] = mapped_column(String, nullable=True)
    fixed_ip: Mapped[str | None] = mapped_column(String, nullable=True)
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
    security_group_id: Mapped[str] = mapped_column(String, ForeignKey("networking_security_groups.id"))
    direction: Mapped[str] = mapped_column(String)
    ethertype: Mapped[str] = mapped_column(String, default="IPv4")
    protocol: Mapped[str] = mapped_column(String, default="any")
    port_min: Mapped[str | None] = mapped_column(String, nullable=True)
    port_max: Mapped[str | None] = mapped_column(String, nullable=True)
    remote_ip_prefix: Mapped[str | None] = mapped_column(String, nullable=True)
    remote_group_id: Mapped[str | None] = mapped_column(String, nullable=True)
    action: Mapped[str] = mapped_column(String, default="allow")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


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


class RouterIn(BaseModel):
    name: str


class RouterInterfaceIn(BaseModel):
    subnet: str


class PortIn(BaseModel):
    name: str
    network: str
    ip_address: str | None = None
    fixed_ip: str | None = None
    device_id: str | None = None
    device_owner: str | None = None
    security_group_ids: list[str] = Field(default_factory=list)
    port_security_enabled: bool = True


class FloatingIPIn(BaseModel):
    name: str | None = None
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


def port_to_dict(db: Session, port: Port) -> dict:
    data = obj_to_dict(port)
    network = db.get(Network, port.network_id)
    subnet = db.get(Subnet, port.subnet_id) if port.subnet_id else None
    data["fixed_ip"] = port.fixed_ip or port.ip_address
    data["ip_address"] = port.ip_address or port.fixed_ip
    data["logical_switch_name"] = port.logical_switch_name or (network.name if network else port.network_id)
    data["logical_port_name"] = port.logical_port_name or port.name
    data["security_group_ids"] = parse_id_list(port.security_group_ids)
    if subnet:
        data["cidr"] = subnet.cidr
        data["gateway_ip"] = subnet.gateway_ip or subnet.gateway
        data["dns_nameservers"] = subnet_dns(subnet)
    return data


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


def provider_config() -> dict:
    return {
        "interface": PROVIDER_INTERFACE,
        "bridge": PROVIDER_BRIDGE,
        "network_name": PROVIDER_NETWORK_NAME,
        "subnet_name": PROVIDER_SUBNET_NAME,
        "cidr": PROVIDER_CIDR,
        "gateway": PROVIDER_GATEWAY,
        "allocation_start": PROVIDER_ALLOCATION_START,
        "allocation_end": PROVIDER_ALLOCATION_END,
        "dns_nameservers": PROVIDER_DNS,
        "enable_dhcp": PROVIDER_ENABLE_DHCP,
        "physical_network": PROVIDER_PHYSNET,
    }


def external_network_defaults(name: str) -> dict:
    config = provider_config()
    return {
        "name": name,
        "external": True,
        "provider": True,
        "network_type": "external",
        "physical_network": config["physical_network"],
        "bridge_mapping": f'{config["physical_network"]}:{config["bridge"]}',
        "provider_bridge": config["bridge"],
        "provider_cidr": config["cidr"],
        "gateway_ip": config["gateway"],
        "allocation_start": config["allocation_start"],
        "allocation_end": config["allocation_end"],
    }


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


def allocation_bounds(subnet: Subnet) -> tuple[ipaddress.IPv4Address, ipaddress.IPv4Address]:
    network = ipaddress.ip_network(subnet.cidr, strict=False)
    hosts = list(network.hosts())
    if not hosts:
        raise HTTPException(status_code=400, detail=f"subnet '{subnet.name}' has no usable host addresses")
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


def allocate_floating_ip(db: Session, network: Network) -> str:
    if not network.provider_cidr:
        raise HTTPException(status_code=400, detail="external network has no provider_cidr")
    start, end = allocation_bounds_for_network(network.provider_cidr, network.gateway_ip, network.allocation_start, network.allocation_end)
    current = ipaddress.ip_address(start)
    end_ip = ipaddress.ip_address(end)
    used = {
        ipaddress.ip_address(ip)
        for ip in db.scalars(select(FloatingIP.floating_ip).where(FloatingIP.external_network_id == network.id)).all()
        if ip
    }
    used.update(
        ipaddress.ip_address(ip)
        for ip in db.scalars(select(FloatingIP.floating_ip_address).where(FloatingIP.external_network_id == network.id)).all()
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
        port = db.scalar(select(Port).where(Port.device_id == payload.server).order_by(Port.created_at))
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
    gateway_ip = ipaddress.ip_address(subnet.gateway_ip or subnet.gateway)
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
        if candidate == gateway_ip or candidate in used:
            raise HTTPException(status_code=409, detail=f"requested IP {requested} is not available")
        return str(candidate)
    start, end = allocation_bounds(subnet)
    current = start
    while current <= end:
        if current != gateway_ip and current not in used:
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
                ("provider", "BOOLEAN DEFAULT FALSE"),
                ("network_type", "VARCHAR"),
                ("physical_network", "VARCHAR"),
                ("bridge_mapping", "VARCHAR"),
                ("provider_bridge", "VARCHAR"),
                ("provider_cidr", "VARCHAR"),
                ("gateway_ip", "VARCHAR"),
                ("allocation_start", "VARCHAR"),
                ("allocation_end", "VARCHAR"),
            ):
                if column not in columns:
                    conn.execute(text(f"ALTER TABLE networking_networks ADD COLUMN {column} {ddl}"))
        if "networking_subnets" in table_names:
            columns = {column["name"] for column in inspector.get_columns("networking_subnets")}
            for column in ("gateway_ip", "allocation_start", "allocation_end", "dns_nameservers"):
                if column not in columns:
                    conn.execute(text(f"ALTER TABLE networking_subnets ADD COLUMN {column} VARCHAR"))
            for column, ddl in (("enable_dhcp", "BOOLEAN DEFAULT TRUE"), ("ovn_dhcp_options_uuid", "VARCHAR")):
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


def bootstrap_provider_network() -> None:
    db = SessionLocal()
    try:
        existing = get_by_ref(db, Network, PROVIDER_NETWORK_NAME)
        if not existing:
            defaults = external_network_defaults(PROVIDER_NETWORK_NAME)
            start, end = allocation_bounds_for_network(defaults["provider_cidr"], defaults["gateway_ip"], defaults["allocation_start"], defaults["allocation_end"])
            network = Network(**{**defaults, "allocation_start": start, "allocation_end": end})
            db.add(network)
            db.commit()
            db.refresh(network)
        else:
            network = existing
            changed = False
            for field, value in external_network_defaults(network.name).items():
                if getattr(network, field, None) in (None, "", False) and value not in (None, ""):
                    setattr(network, field, value)
                    changed = True
            if changed:
                db.commit()
                db.refresh(network)
        ensure_provider_ovn(network.name, network.physical_network or PROVIDER_PHYSNET, network.provider_bridge or PROVIDER_BRIDGE)
        subnet = get_by_ref(db, Subnet, PROVIDER_SUBNET_NAME)
        if not subnet:
            dhcp_id = ovn(*dhcp_options_args(PROVIDER_CIDR, PROVIDER_GATEWAY, PROVIDER_DNS)) if PROVIDER_ENABLE_DHCP else None
            subnet = Subnet(
                name=PROVIDER_SUBNET_NAME,
                network_id=network.id,
                cidr=PROVIDER_CIDR,
                gateway=PROVIDER_GATEWAY,
                gateway_ip=PROVIDER_GATEWAY,
                allocation_start=PROVIDER_ALLOCATION_START,
                allocation_end=PROVIDER_ALLOCATION_END,
                dns_nameservers=",".join(PROVIDER_DNS) or None,
                dhcp_options_id=dhcp_id,
                enable_dhcp=PROVIDER_ENABLE_DHCP,
                ovn_dhcp_options_uuid=dhcp_id,
            )
            db.add(subnet)
            db.commit()
    finally:
        db.close()


app = FastAPI(title="cloudx networking-api", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)
    ensure_columns()
    if PROVIDER_NETWORK_AUTO_CREATE:
        try:
            bootstrap_provider_network()
        except Exception as exc:
            print(f"provider network bootstrap skipped: {exc}", flush=True)
    else:
        print("provider network auto-create disabled (PROVIDER_NETWORK_AUTO_CREATE=false); create via CLI", flush=True)
    try:
        bootstrap_default_security_group()
    except Exception as exc:
        print(f"default security group bootstrap skipped: {exc}", flush=True)


@app.get("/health")
def health():
    return {"status": "ok", "service": "networking-api", "provider": provider_config()}


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
    )
    if payload.external:
        ensure_provider_ovn(payload.name, provider_defaults["physical_network"], provider_defaults["provider_bridge"])
    else:
        ovn("--may-exist", "ls-add", payload.name)
        ovn("set", "logical_switch", payload.name, "external_ids:mini-openstack-lab=true")
    db.add(network)
    db.commit()
    db.refresh(network)
    return obj_to_dict(network)


@app.get("/networks")
def list_networks(_: CurrentUser, db: DB):
    return {"items": [obj_to_dict(n) for n in db.scalars(select(Network).order_by(Network.created_at)).all()]}


@app.get("/networks/{ref}")
def show_network(ref: str, _: CurrentUser, db: DB):
    return obj_to_dict(require_by_ref(db, Network, ref, "network"))


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
    if not gateway:
        raise HTTPException(status_code=400, detail="gateway_ip or gateway is required")
    try:
        cidr = ipaddress.ip_network(payload.cidr, strict=False)
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
    dhcp_id = ovn(*dhcp_options_args(payload.cidr, gateway, payload.dns_nameservers)) if payload.enable_dhcp else None
    subnet = Subnet(
        name=payload.name,
        network_id=network.id,
        cidr=payload.cidr,
        gateway=gateway,
        gateway_ip=gateway,
        allocation_start=str(allocation_start) if allocation_start else None,
        allocation_end=str(allocation_end) if allocation_end else None,
        dns_nameservers=dns or None,
        dhcp_options_id=dhcp_id,
        enable_dhcp=payload.enable_dhcp,
        ovn_dhcp_options_uuid=dhcp_id,
    )
    db.add(subnet)
    db.commit()
    db.refresh(subnet)
    return obj_to_dict(subnet)


@app.get("/subnets")
def list_subnets(_: CurrentUser, db: DB):
    return {"items": [obj_to_dict(s) for s in db.scalars(select(Subnet).order_by(Subnet.created_at)).all()]}


@app.get("/subnets/{ref}")
def show_subnet(ref: str, _: CurrentUser, db: DB):
    data = obj_to_dict(require_by_ref(db, Subnet, ref, "subnet"))
    data["ovn_dhcp_options_uuid"] = data.get("ovn_dhcp_options_uuid") or data.get("dhcp_options_id")
    return data


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
def list_routers(_: CurrentUser, db: DB):
    return {"items": [obj_to_dict(r) for r in db.scalars(select(Router).order_by(Router.created_at)).all()]}


@app.get("/routers/{ref}")
def show_router(ref: str, _: CurrentUser, db: DB):
    return obj_to_dict(require_by_ref(db, Router, ref, "router"))


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
def create_port(payload: PortIn, db: DB):
    if get_by_ref(db, Port, payload.name):
        raise HTTPException(status_code=409, detail="port already exists")
    network = require_by_ref(db, Network, payload.network, "network")
    subnet = db.scalar(select(Subnet).where(Subnet.network_id == network.id).order_by(Subnet.created_at))
    requested_ip = payload.fixed_ip or payload.ip_address
    fixed_ip = allocate_fixed_ip(db, subnet, requested_ip) if subnet else requested_ip
    port_mac = mac()
    logical_switch_name = network.name
    logical_port_name = payload.name
    security_group_ids = payload.security_group_ids or ["default"]
    groups = [require_by_ref(db, SecurityGroup, group_id, "security-group") for group_id in security_group_ids]
    port = Port(
        name=payload.name,
        network_id=network.id,
        subnet_id=subnet.id if subnet else None,
        mac_address=port_mac,
        ip_address=fixed_ip,
        fixed_ip=fixed_ip,
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
    ovn("lsp-set-addresses", port.name, f"{port.mac_address} {fixed_ip}" if fixed_ip else port.mac_address)
    if subnet and subnet.dhcp_options_id:
        ovn("lsp-set-dhcpv4-options", port.name, subnet.ovn_dhcp_options_uuid or subnet.dhcp_options_id)
    db.add(port)
    db.commit()
    db.refresh(port)
    apply_security_groups(db, port)
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


@app.post("/floating-ips", status_code=201)
def create_floating_ip(payload: FloatingIPIn, current: CurrentUser, db: DB):
    if payload.name and get_by_ref(db, FloatingIP, payload.name):
        raise HTTPException(status_code=409, detail="floating-ip already exists")
    external_network = require_by_ref(db, Network, payload.external_network, "network") if payload.external_network else None
    if external_network and not external_network.external:
        raise HTTPException(status_code=400, detail="floating IP network must be external")
    floating_ip = payload.floating_ip or (allocate_floating_ip(db, external_network) if external_network else None)
    if not floating_ip:
        raise HTTPException(status_code=400, detail="floating_ip or external_network is required")
    name = payload.name or f"fip-{floating_ip.replace('.', '-')}"
    if get_by_ref(db, FloatingIP, name):
        raise HTTPException(status_code=409, detail="floating-ip already exists")
    port = require_by_ref(db, Port, payload.fixed_port, "port") if payload.fixed_port else None
    fip = FloatingIP(
        name=name,
        floating_ip=floating_ip,
        floating_ip_address=floating_ip,
        fixed_ip_address=port.fixed_ip if port else None,
        fixed_port_id=port.id if port else None,
        port_id=port.id if port else None,
        server_id=port.device_id if port else None,
        external_network_id=external_network.id if external_network else None,
        status="DOWN",
    )
    db.add(fip)
    db.commit()
    db.refresh(fip)
    return obj_to_dict(fip)


@app.get("/floating-ips")
def list_floating_ips(_: CurrentUser, db: DB):
    return {"items": [obj_to_dict(f) for f in db.scalars(select(FloatingIP).order_by(FloatingIP.created_at)).all()]}


@app.get("/floating-ips/{ref}")
def show_floating_ip(ref: str, _: CurrentUser, db: DB):
    return obj_to_dict(require_floating_ip(db, ref))


@app.post("/floating-ips/{ref}/associate")
def associate_floating_ip(ref: str, payload: FloatingIPAssociateIn, _: CurrentUser, db: DB):
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
    return obj_to_dict(fip)


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
    return obj_to_dict(fip)


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
    group = SecurityGroup(name=payload.name, description=payload.description, project_id=payload.project_id)
    db.add(group)
    db.commit()
    db.refresh(group)
    apply_security_group_acls(db, group)
    return obj_to_dict(group)


@app.get("/security-groups")
def list_security_groups(_: CurrentUser, db: DB):
    return {"items": [obj_to_dict(group) for group in db.scalars(select(SecurityGroup).order_by(SecurityGroup.created_at)).all()]}


@app.get("/security-groups/{ref}")
def show_security_group(ref: str, _: CurrentUser, db: DB):
    group = require_by_ref(db, SecurityGroup, ref, "security-group")
    data = obj_to_dict(group)
    data["rules"] = [obj_to_dict(rule) for rule in db.scalars(select(SecurityGroupRule).where(SecurityGroupRule.security_group_id == group.id)).all()]
    return data


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
