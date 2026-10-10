from fastapi import APIRouter, HTTPException
from shared.auth_middleware import CurrentUser
from sqlalchemy import select

from .. import ovn_cli
from ..access import own
from ..db import DB, SessionLocal
from ..models import FloatingIP, PortForwarding, Router
from ..schemas import PortForwardingIn
from ..serializers import require_floating_ip

router = APIRouter()

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
    # We need a router name — look it up
    db_temp = SessionLocal()
    try:
        router = db_temp.get(Router, fip.router_id)
        if not router:
            return
        external_ip = fip.floating_ip_address or fip.floating_ip
        if delete:
            ovn_cli.ovn(
                "--if-exists", "lr-nat-del", router.name, "dnat",
                f"{external_ip}:{pf.external_port}",
                check=False,
            )
        else:
            ovn_cli.ovn(
                "--may-exist", "lr-nat-add", router.name, "dnat",
                f"{external_ip}:{pf.external_port}",
                f"{pf.internal_ip_address}:{pf.internal_port}",
                f"--protocol={pf.protocol}",
                check=False,
            )
    finally:
        db_temp.close()

@router.post("/floatingips/{fip_ref}/port_forwardings", status_code=201)
def create_port_forwarding(fip_ref: str, payload: PortForwardingIn, current: CurrentUser, db: DB):
    """Create a DNAT port forwarding rule on a floating IP."""
    fip = own(current, require_floating_ip(db, fip_ref), "floating-ip")
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

@router.get("/floatingips/{fip_ref}/port_forwardings")
def list_port_forwardings(fip_ref: str, current: CurrentUser, db: DB):
    """List all port forwarding rules for a floating IP."""
    fip = own(current, require_floating_ip(db, fip_ref), "floating-ip")
    pfs = db.scalars(
        select(PortForwarding).where(PortForwarding.floatingip_id == fip.id).order_by(PortForwarding.created_at)
    ).all()
    return {"port_forwardings": [_pf_to_dict(pf) for pf in pfs]}

@router.get("/floatingips/{fip_ref}/port_forwardings/{pf_id}")
def show_port_forwarding(fip_ref: str, pf_id: str, current: CurrentUser, db: DB):
    """Get a specific port forwarding rule."""
    fip = own(current, require_floating_ip(db, fip_ref), "floating-ip")
    pf = db.get(PortForwarding, pf_id)
    if not pf or pf.floatingip_id != fip.id:
        raise HTTPException(status_code=404, detail="port forwarding not found")
    return _pf_to_dict(pf)

@router.delete("/floatingips/{fip_ref}/port_forwardings/{pf_id}", status_code=204)
def delete_port_forwarding(fip_ref: str, pf_id: str, current: CurrentUser, db: DB):
    """Delete a port forwarding rule and remove OVN DNAT entry."""
    fip = own(current, require_floating_ip(db, fip_ref), "floating-ip")
    pf = db.get(PortForwarding, pf_id)
    if not pf or pf.floatingip_id != fip.id:
        raise HTTPException(status_code=404, detail="port forwarding not found")
    _apply_pf_ovn_rule(fip, pf, delete=True)
    db.delete(pf)
    db.commit()
