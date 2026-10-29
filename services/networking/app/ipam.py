import ipaddress

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .models import FloatingIP, Network, Port, Router, Subnet
from .schemas import FloatingIPAssociateIn
from .serializers import require_by_ref


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

def _host_range(network):
    """First and last usable address, like ipaddress's hosts() but without enumerating them.

    list(network.hosts()) is 2**64 addresses for an IPv6 /64 (and 16 million for an IPv4 /8): it never returns.
    Returns None when the network has no usable address.
    """
    first, last = network.network_address, network.broadcast_address
    if network.version == 4:
        if network.prefixlen < 31:           # /31 and /32 have no network/broadcast address to skip
            first, last = first + 1, last - 1
    elif network.prefixlen < 127:            # IPv6: only the subnet-router anycast address (network address) is skipped
        first = first + 1
    return (first, last) if first <= last else None


def allocation_bounds(subnet: Subnet) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Return (start, end) IP allocation bounds for an IPv4 or IPv6 subnet."""
    network = ipaddress.ip_network(subnet.cidr, strict=False)
    usable = _host_range(network)
    if usable is None:
        raise HTTPException(status_code=400, detail=f"subnet '{subnet.name}' has no usable host addresses")
    first, last = usable

    if network.version == 6:
        start = ipaddress.ip_address(subnet.allocation_start) if subnet.allocation_start else first
    else:
        # IPv4: the first usable address is conventionally the gateway, so allocation starts at the second
        start = ipaddress.ip_address(subnet.allocation_start) if subnet.allocation_start else (first + 1 if last > first else first)
    end = ipaddress.ip_address(subnet.allocation_end) if subnet.allocation_end else last

    if start not in network or end not in network or start > end:
        raise HTTPException(status_code=400, detail="invalid allocation range for subnet")
    return start, end

def allocation_bounds_for_network(cidr_value: str, gateway_value: str | None, start_value: str | None, end_value: str | None) -> tuple[str, str]:
    network = ipaddress.ip_network(cidr_value, strict=False)
    usable = _host_range(network)
    if usable is None:
        raise HTTPException(status_code=400, detail=f"network {cidr_value} has no usable addresses")
    first, last = usable
    gateway_ip = ipaddress.ip_address(gateway_value) if gateway_value else first
    if start_value:
        start = ipaddress.ip_address(start_value)
    elif first != gateway_ip:
        start = first
    else:
        start = first + 1 if first + 1 <= last else None   # the first usable address that is not the gateway
    end = ipaddress.ip_address(end_value) if end_value else last
    if start is None:
        raise HTTPException(status_code=400, detail="no usable allocation start")
    if gateway_ip not in network or start not in network or end not in network or start > end:
        raise HTTPException(status_code=400, detail="invalid allocation range")
    return str(start), str(end)

def allocate_floating_ip(db: Session, network: Network) -> str:
    # Prefer subnet associated with the external network
    subnet = db.scalar(
        select(Subnet).where(Subnet.network_id == network.id).order_by(Subnet.created_at)
    )
    cidr = (subnet.cidr if subnet and subnet.cidr else None) or network.provider_cidr
    if not cidr:
        raise HTTPException(status_code=400, detail="external network has no provider_cidr or subnet")
    gw = (subnet.gateway_ip if subnet and subnet.gateway_ip else None) or (subnet.gateway if subnet and subnet.gateway else None) or network.gateway_ip
    start = (subnet.allocation_start if subnet and subnet.allocation_start else None) or network.allocation_start
    end = (subnet.allocation_end if subnet and subnet.allocation_end else None) or network.allocation_end

    start, end = allocation_bounds_for_network(cidr, gw, start, end)
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
        if str(current) != gw and current not in used:
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

def _used_addresses(db: Session, subnet: Subnet, network) -> set:
    if network.version == 6:
        # A dual-stack port keeps its IPv6 address in ipv6_address while subnet_id points at its IPv4 subnet, so an
        # IPv6 subnet is matched by network and range instead of by subnet_id.
        rows = db.execute(
            select(Port.ipv6_address, Port.fixed_ip, Port.ip_address).where(Port.network_id == subnet.network_id)
        ).all()
        used = set()
        for value in (v for row in rows for v in row if v):
            try:
                address = ipaddress.ip_address(value)
            except ValueError:
                continue
            if address in network:
                used.add(address)
        return used
    used = {
        ipaddress.ip_address(ip)
        for ip in db.scalars(select(Port.fixed_ip).where(Port.subnet_id == subnet.id, Port.fixed_ip.is_not(None))).all()
    }
    used.update(
        ipaddress.ip_address(ip)
        for ip in db.scalars(select(Port.ip_address).where(Port.subnet_id == subnet.id, Port.ip_address.is_not(None))).all()
    )
    return used


def allocate_fixed_ip(db: Session, subnet: Subnet, requested: str | None = None) -> str:
    if db.get_bind().dialect.name == "postgresql":
        # Serialize allocation per subnet across gunicorn workers; released at the caller's commit/rollback.
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": subnet.id})
    network = ipaddress.ip_network(subnet.cidr, strict=False)
    gateway_str = subnet.gateway_ip or subnet.gateway
    gateway_ip = ipaddress.ip_address(gateway_str) if gateway_str else None
    used = _used_addresses(db, subnet, network)
    if requested:
        try:
            candidate = ipaddress.ip_address(requested)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"requested IP {requested} is not an IP address") from None
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
