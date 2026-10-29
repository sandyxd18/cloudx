import ipaddress
import json
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    FloatingIP,
    Network,
    Port,
    Router,
    RouterInterface,
    SecurityGroup,
    SecurityGroupRule,
    Subnet,
)


def obj_to_dict(obj):
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}

def _fmt_ts(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

def network_mtu(network: Network) -> int:
    return network.mtu or (1500 if network.external else 1442)

def _ip_version(cidr: str) -> int:
    try:
        return ipaddress.ip_network(cidr, strict=False).version
    except ValueError:
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
        "mtu": network_mtu(network),
        "name": network.name,
        "port_security_enabled": True,
        "project_id": network.project_id,
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
        "project_id": subnet.project_id,
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
        "project_id": router.project_id,
        "revision_number": 4,
        "routes": json.loads(router.routes or "[]"),
        "status": "ACTIVE",
        "tags": [],
        "updated_at": _fmt_ts(router.created_at),
    }

def port_to_os_dict(db: Session, port: Port, current: dict | None = None) -> dict:
    """Serialize a Port to an OpenStack-compatible dict."""
    network = db.get(Network, port.network_id)
    subnet = db.get(Subnet, port.subnet_id) if port.subnet_id else None
    ipv6_subnet = None
    if port.ipv6_address:
        # the IPv6 address is not tied to subnet_id (that is the IPv4 subnet of a dual-stack port): find it by range
        try:
            address = ipaddress.ip_address(port.ipv6_address)
            for candidate in db.scalars(select(Subnet).where(Subnet.network_id == port.network_id, Subnet.ip_version == 6)):
                if address in ipaddress.ip_network(candidate.cidr, strict=False):
                    ipv6_subnet = candidate
                    break
        except ValueError:
            pass
    if subnet is not None and subnet.ip_version != 4:
        subnet = None  # a port on an IPv6-only network points subnet_id at its IPv6 subnet; cidr/gateway_ip below are IPv4

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
        "allowed_address_pairs": json.loads(port.allowed_address_pairs or "[]"),
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
        "qos_policy": {"max_kbps": port.qos_max_kbps, "burst_kbps": port.qos_burst_kbps} if (port.qos_max_kbps or port.qos_burst_kbps) else None,
        "project_id": port.project_id,
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
        "ipv6_cidr": ipv6_subnet.cidr if ipv6_subnet else None,
        "ipv6_gateway_ip": (ipv6_subnet.gateway_ip or ipv6_subnet.gateway or None) if ipv6_subnet else None,
        "ipv6_dns_nameservers": ([s.strip() for s in ipv6_subnet.dns_nameservers.split(",") if s.strip()] if ipv6_subnet and ipv6_subnet.dns_nameservers else []),
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
        "project_id": fip.project_id,
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

def get_by_ref(db: Session, model, ref: str, current: dict | None = None):
    obj = db.get(model, ref)
    if obj:
        return obj

    if not hasattr(model, "name"):
        return None

    matches = db.scalars(select(model).where(model.name == ref)).all()
    if not matches:
        return None

    pid = current.get("project_id", current.get("tenant_id")) if current else None
    role = current.get("role") if current else None

    if pid:
        proj_matches = [m for m in matches if getattr(m, "project_id", None) == pid]
        if len(proj_matches) == 1:
            return proj_matches[0]
        if len(proj_matches) > 1:
            lbl = getattr(model, "__tablename__", "resource")
            raise HTTPException(status_code=409, detail=f"multiple {lbl} found with name '{ref}' in project")

        fallback = [
            m for m in matches
            if getattr(m, "project_id", None) is None
            or getattr(m, "shared", False)
            or getattr(m, "external", False)
        ]
        if len(fallback) == 1:
            return fallback[0]
        if len(fallback) > 1:
            lbl = getattr(model, "__tablename__", "resource")
            raise HTTPException(status_code=409, detail=f"multiple {lbl} found with name '{ref}'")
        if role != "admin":
            return None

    if len(matches) == 1:
        return matches[0]

    lbl = getattr(model, "__tablename__", "resource")
    raise HTTPException(status_code=409, detail=f"multiple {lbl} found with name '{ref}'; specify ID instead")

def get_floating_ip(db: Session, ref: str, current: dict | None = None):
    fip = db.get(FloatingIP, ref)
    if fip:
        return fip
    fip = get_by_ref(db, FloatingIP, ref, current=current)
    if fip:
        return fip
    return (
        db.scalar(select(FloatingIP).where(FloatingIP.floating_ip == ref))
        or db.scalar(select(FloatingIP).where(FloatingIP.floating_ip_address == ref))
    )

def require_floating_ip(db: Session, ref: str, current: dict | None = None):
    fip = get_floating_ip(db, ref, current=current)
    if not fip:
        raise HTTPException(status_code=404, detail=f"floating-ip '{ref}' not found")
    return fip

def require_by_ref(db: Session, model, ref: str, label: str, current: dict | None = None):
    obj = get_by_ref(db, model, ref, current=current)
    if not obj:
        raise HTTPException(status_code=404, detail=f"{label} '{ref}' not found")
    return obj
