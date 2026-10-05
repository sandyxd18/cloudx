import uuid

from fastapi import APIRouter, HTTPException
from shared.auth_middleware import CurrentUser
from sqlalchemy import select

from .. import ovn_cli
from ..config import log
from ..db import DB
from ..ipam import allocate_floating_ip, find_router_for_fip, resolve_association_port
from ..models import FloatingIP, Network, Port, Router
from ..ovn_cli import nat_rule_args
from ..schemas import FloatingIPAssociateIn, FloatingIPIn
from ..serializers import (
    fip_to_os_dict,
    get_by_ref,
    obj_to_dict,
    require_by_ref,
    require_floating_ip,
)

router = APIRouter()


@router.post("/floating-ips", status_code=201)
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
                ovn_cli.ovn(*nat_rule_args(router.name, floating_ip, fixed_ip))
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

@router.get("/floating-ips")
def list_floating_ips(current: CurrentUser, db: DB):
    return {"items": [fip_to_os_dict(db, f, current) for f in db.scalars(select(FloatingIP).order_by(FloatingIP.created_at)).all()]}

@router.get("/floating-ips/{ref}")
def show_floating_ip(ref: str, current: CurrentUser, db: DB):
    return fip_to_os_dict(db, require_floating_ip(db, ref), current)

@router.post("/floating-ips/{ref}/associate")
def associate_floating_ip(ref: str, payload: FloatingIPAssociateIn, current: CurrentUser, db: DB):
    fip = require_floating_ip(db, ref)
    port = resolve_association_port(db, payload)
    router = find_router_for_fip(db, payload.router)
    fixed_ip = port.fixed_ip or port.ip_address
    if not fixed_ip:
        raise HTTPException(status_code=400, detail="port has no fixed IP")
    ovn_cli.ovn(*nat_rule_args(router.name, fip.floating_ip, fixed_ip))
    fip.fixed_port_id = port.id
    fip.port_id = port.id
    fip.fixed_ip_address = fixed_ip
    fip.server_id = port.device_id
    fip.router_id = router.id
    fip.status = "ACTIVE"
    db.commit()
    db.refresh(fip)
    return fip_to_os_dict(db, fip, current)

@router.post("/floating-ips/{ref}/disassociate")
def disassociate_floating_ip(ref: str, _: CurrentUser, db: DB):
    fip = require_floating_ip(db, ref)
    router = db.get(Router, fip.router_id) if fip.router_id else None
    if router and fip.floating_ip:
        ovn_cli.ovn("--if-exists", "lr-nat-del", router.name, "dnat_and_snat", fip.floating_ip, check=False)
    fip.fixed_port_id = None
    fip.port_id = None
    fip.fixed_ip_address = None
    fip.server_id = None
    fip.router_id = None
    fip.status = "DOWN"
    db.commit()
    db.refresh(fip)
    return fip_to_os_dict(db, fip)

@router.delete("/floating-ips/{ref}")
def delete_floating_ip(ref: str, _: CurrentUser, db: DB):
    fip = require_floating_ip(db, ref)
    if fip.router_id and fip.floating_ip:
        router = db.get(Router, fip.router_id)
        if router:
            ovn_cli.ovn("--if-exists", "lr-nat-del", router.name, "dnat_and_snat", fip.floating_ip, check=False)
    db.delete(fip)
    db.commit()
    return {"status": "deleted", "id": fip.id, "name": fip.name}
