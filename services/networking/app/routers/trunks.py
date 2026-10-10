from fastapi import APIRouter, HTTPException
from shared.auth_middleware import CurrentUser
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import ovn_cli
from ..access import own, scope
from ..db import DB
from ..models import Port, Trunk, TrunkSubPort
from ..schemas import TrunkIn, TrunkSubPortsPayload
from ..serializers import _fmt_ts, get_by_ref, require_by_ref

router = APIRouter()

# ── Trunk Ports ──────────────────────────────────────────────────────────────
# Trunk ports allow a parent port to carry multiple VLANs via sub-ports.
# Each sub-port is a regular port with a VLAN tag set in OVN.
# Reference: Network trunk ports specification

def _trunk(db, current, ref):
    return own(current, require_by_ref(db, Trunk, ref, "trunk", current=current), "trunk")


def _nf():
    raise HTTPException(status_code=404, detail="trunk not found")


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

@router.post("/trunks", status_code=201)
def create_trunk(payload: TrunkIn, current: CurrentUser, db: DB):
    """Create a trunk port. The parent port must already exist."""
    parent_port = own(current, require_by_ref(db, Port, payload.port_id, "port", current=current), "port")
    if get_by_ref(db, Trunk, payload.name, current=current):
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

@router.get("/trunks")
def list_trunks(current: CurrentUser, db: DB):
    """List all trunk ports."""
    trunks = db.scalars(scope(select(Trunk), Trunk, current).order_by(Trunk.created_at)).all()
    return {"trunks": [_trunk_to_dict(db, t) for t in trunks]}

@router.get("/trunks/{trunk_ref}")
def show_trunk(trunk_ref: str, current: CurrentUser, db: DB):
    """Get details of a specific trunk."""
    trunk = _trunk(db, current, trunk_ref)
    return _trunk_to_dict(db, trunk)

@router.delete("/trunks/{trunk_ref}", status_code=204)
def delete_trunk(trunk_ref: str, current: CurrentUser, db: DB):
    """Delete a trunk and all its sub-port associations."""
    trunk = _trunk(db, current, trunk_ref)
    # Ensure no active sub-ports remain
    sub_count = db.scalar(select(TrunkSubPort).where(TrunkSubPort.trunk_id == trunk.id))
    if sub_count:
        raise HTTPException(status_code=409, detail="remove all sub_ports before deleting the trunk")
    db.delete(trunk)
    db.commit()

@router.put("/trunks/{trunk_ref}/add_subports")
def add_subports(trunk_ref: str, payload: TrunkSubPortsPayload, current: CurrentUser, db: DB):
    """
    Add sub-ports to a trunk.
    Each sub-port is an existing port that will be VLAN-tagged in OVN.
    """
    trunk = _trunk(db, current, trunk_ref)

    added = []
    for sp_in in payload.sub_ports:
        # Validate sub-port exists
        sub_port = own(current, require_by_ref(db, Port, sp_in.port_id, "port"), "port")
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
        ovn_cli.ovn("set", "Logical_Switch_Port", sub_port.name,
            f"tag={sp_in.segmentation_id}", check=False)

        added.append({
            "port_id": sub_port.id,
            "segmentation_type": sp_in.segmentation_type,
            "segmentation_id": sp_in.segmentation_id,
        })

    db.commit()
    return {"sub_ports": added}

@router.put("/trunks/{trunk_ref}/remove_subports")
def remove_subports(trunk_ref: str, payload: TrunkSubPortsPayload, current: CurrentUser, db: DB):
    """Remove sub-ports from a trunk and clear their VLAN tags in OVN."""
    trunk = _trunk(db, current, trunk_ref)

    removed = []
    for sp_in in payload.sub_ports:
        sub_port = db.get(Port, sp_in.port_id)
        if not sub_port or not own(current, sub_port, "port"):
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
            ovn_cli.ovn("clear", "Logical_Switch_Port", sub_port.name, "tag", check=False)
            removed.append({"port_id": sub_port.id})

    db.commit()
    return {"sub_ports": removed}
