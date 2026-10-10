import ipaddress

from fastapi import APIRouter, HTTPException
from shared.auth_middleware import CurrentUser
from sqlalchemy import select

from .. import ovn_cli
from ..access import require_owned, scope
from ..db import DB
from ..quota import check_quota
from ..models import Network, Port, RouterInterface, Subnet
from ..ovn_cli import _ensure_dhcpv6_options, dhcp_options_args
from ..schemas import SubnetIn
from ..serializers import get_by_ref, network_mtu, obj_to_dict, subnet_to_os_dict

router = APIRouter()


@router.post("/subnets", status_code=201)
def create_subnet(payload: SubnetIn, current: CurrentUser, db: DB):
    if get_by_ref(db, Subnet, payload.name, current=current):
        raise HTTPException(status_code=409, detail="subnet already exists")
    check_quota(db, current.get("project_id"), Subnet, "subnets")
    network = require_owned(current, db, Network, payload.network, "network")
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
            dhcp_id = ovn_cli.ovn(*dhcp_options_args(payload.cidr, gateway, payload.dns_nameservers, mtu=network_mtu(network))) if gateway else None
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
        project_id=current.get("project_id"),
    )
    db.add(subnet)
    if network.external:
        network.provider_cidr = payload.cidr
        network.gateway_ip = gateway or network.gateway_ip
        if allocation_start:
            network.allocation_start = str(allocation_start)
        if allocation_end:
            network.allocation_end = str(allocation_end)
    db.commit()
    db.refresh(subnet)
    return obj_to_dict(subnet)

@router.get("/subnets")
def list_subnets(current: CurrentUser, db: DB):
    return {"items": [subnet_to_os_dict(db, s, current) for s in db.scalars(scope(select(Subnet), Subnet, current).order_by(Subnet.created_at)).all()]}

@router.get("/subnets/{ref}")
def show_subnet(ref: str, current: CurrentUser, db: DB):
    return subnet_to_os_dict(db, require_owned(current, db, Subnet, ref, "subnet"), current)

@router.delete("/subnets/{ref}")
def delete_subnet(ref: str, current: CurrentUser, db: DB):
    subnet = require_owned(current, db, Subnet, ref, "subnet")
    if db.scalar(select(Port.id).where(Port.subnet_id == subnet.id).limit(1)):
        raise HTTPException(status_code=409, detail="subnet still has ports; delete them first")
    if db.scalar(select(RouterInterface.id).where(RouterInterface.subnet_id == subnet.id).limit(1)):
        raise HTTPException(status_code=409, detail="subnet is attached to a router; detach it first")
    if subnet.ovn_dhcp_options_uuid or subnet.dhcp_options_id:
        ovn_cli.ovn("--if-exists", "destroy", "DHCP_Options", subnet.ovn_dhcp_options_uuid or subnet.dhcp_options_id, check=False)
    db.delete(subnet)
    db.commit()
    return {"status": "deleted", "id": subnet.id, "name": subnet.name}
