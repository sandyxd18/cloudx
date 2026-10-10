from fastapi import APIRouter, HTTPException
from shared.auth_middleware import CurrentUser
from sqlalchemy import select

from .. import ovn_cli
from ..access import require_owned, scope
from ..db import DB
from ..quota import check_quota
from ..events import _actor, _publish_net_event
from ..ipam import allocation_bounds_for_network
from ..models import Network, Port, Subnet
from ..ovn_cli import ensure_provider_ovn, external_network_defaults
from ..schemas import NetworkIn
from ..serializers import get_by_ref, network_to_os_dict, obj_to_dict

router = APIRouter()


@router.post("/networks", status_code=201)
def create_network(payload: NetworkIn, current: CurrentUser, db: DB):
    if get_by_ref(db, Network, payload.name, current=current):
        raise HTTPException(status_code=409, detail="network already exists")
    check_quota(db, current.get("project_id"), Network, "networks")
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
        mtu=payload.mtu,
    )
    if payload.external:
        ensure_provider_ovn(payload.name, provider_defaults["physical_network"], provider_defaults["provider_bridge"])
    else:
        ovn_cli.ovn("--may-exist", "ls-add", payload.name)
        ovn_cli.ovn("set", "logical_switch", payload.name, "external_ids:cloudx=true")
    db.add(network)
    db.commit()
    db.refresh(network)
    _publish_net_event("network.create.end", {
        "network_id": network.id,
        "name":       network.name,
        "external":   network.external,
        **_actor({**current, "project_id": network.project_id or current.get("project_id")}),
    })
    return obj_to_dict(network)

@router.get("/networks")
def list_networks(current: CurrentUser, db: DB):
    q = scope(select(Network), Network, current).order_by(Network.created_at)
    return {"items": [network_to_os_dict(db, n, current) for n in db.scalars(q).all()]}

@router.get("/networks/{ref}")
def show_network(ref: str, current: CurrentUser, db: DB):
    return network_to_os_dict(db, require_owned(current, db, Network, ref, "network"), current)

@router.delete("/networks/{ref}")
def delete_network(ref: str, current: CurrentUser, db: DB):
    network = require_owned(current, db, Network, ref, "network")
    # check before touching OVN: removing the logical switch and then failing on the database leaves them out of sync
    if db.scalar(select(Subnet.id).where(Subnet.network_id == network.id).limit(1)):
        raise HTTPException(status_code=409, detail="network still has subnets; delete them first")
    if db.scalar(select(Port.id).where(Port.network_id == network.id).limit(1)):
        raise HTTPException(status_code=409, detail="network still has ports; delete them first")
    ovn_cli.ovn("--if-exists", "ls-del", network.name)
    db.delete(network)
    db.commit()
    return {"status": "deleted", "id": network.id, "name": network.name}
