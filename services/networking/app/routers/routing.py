import json

from fastapi import APIRouter, HTTPException
from shared.auth_middleware import CurrentUser
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import ovn_cli
from ..access import own
from ..db import DB
from ..models import BGPPeer, Router, RouterInterface, Subnet, VPCPeering, stable_mac
from ..schemas import BGPPeerIn, BGPPeerOut, VPCPeeringIn, VPCPeeringOut
from ..serializers import require_by_ref

router = APIRouter()


def _pid(current: dict) -> str:
    return current.get("project_id", current.get("tenant_id", "default"))


def _own(obj, current: dict, label: str):
    """404 (not 403) so ids of other projects are not leaked; admin is unrestricted."""
    if not obj or (current.get("role") != "admin" and obj.project_id != _pid(current)):
        raise HTTPException(status_code=404, detail=f"{label} not found")
    return obj


def _bgp_out(peer: BGPPeer) -> dict:
    d = {k: v for k, v in peer.__dict__.items() if k not in ("_sa_instance_state", "auth_password")}
    return {**d, "has_password": bool(peer.auth_password), "advertised_prefixes": json.loads(peer.advertised_prefixes or "[]")}


def _check_router(db: Session, rtr: Router, current: dict) -> None:
    own(current, rtr, "router")


@router.post("/routers/{ref}/peering", response_model=VPCPeeringOut, status_code=201)
def create_vpc_peering(ref: str, payload: VPCPeeringIn, current: CurrentUser, db: DB):
    """Create a VPC peering between two routers."""
    requester = require_by_ref(db, Router, ref, "router")
    accepter = require_by_ref(db, Router, payload.accepter_router_id, "router")
    project_id = _pid(current)
    _check_router(db, requester, current)
    _check_router(db, accepter, current)
    if requester.id == accepter.id:
        raise HTTPException(status_code=400, detail="cannot peer a router with itself")
    dup = db.scalar(select(VPCPeering).where(
        ((VPCPeering.requester_router_id == requester.id) & (VPCPeering.accepter_router_id == accepter.id))
        | ((VPCPeering.requester_router_id == accepter.id) & (VPCPeering.accepter_router_id == requester.id))
    ))
    if dup:
        raise HTTPException(status_code=409, detail="VPC peering already exists")

    peering = VPCPeering(
        name=payload.name,
        project_id=project_id,
        requester_router_id=requester.id,
        accepter_router_id=accepter.id,
        requester_router_name=requester.name,
        accepter_router_name=accepter.name,
        status="pending",
    )
    db.add(peering)
    db.flush()  # assigns the id used for OVN port names; not committed until OVN succeeds

    try:
        _apply_peering_ovn(db, peering)
    except Exception:
        db.rollback()
        raise
    peering.status = "active"
    db.commit()
    db.refresh(peering)
    return peering

def _apply_peering_ovn(db: Session, peering: VPCPeering) -> None:
    """Create OVN patch ports between two peered routers."""
    req_router = db.get(Router, peering.requester_router_id)
    acc_router = db.get(Router, peering.accepter_router_id)
    if not req_router or not acc_router:
        return

    req_port_name = f"lrp-peer-{peering.id[:8]}-req"
    acc_port_name = f"lrp-peer-{peering.id[:8]}-acc"
    req_mac = stable_mac(f"{peering.id}-req")
    acc_mac = stable_mac(f"{peering.id}-acc")

    # Find a unique transit network
    transit_network = "100.100.0.0/30"
    transit_ips = ["100.100.0.1/30", "100.100.0.2/30"]

    # Create router ports on both routers
    ovn_cli.ovn("--may-exist", "lrp-add", req_router.name, req_port_name, req_mac, transit_ips[0])
    ovn_cli.ovn("--may-exist", "lrp-add", acc_router.name, acc_port_name, acc_mac, transit_ips[1])

    # Add static routes for each router's subnets via peer
    req_ifaces = db.scalars(select(RouterInterface).where(RouterInterface.router_id == req_router.id)).all()
    for iface in req_ifaces:
        subnet = db.get(Subnet, iface.subnet_id)
        if subnet:
            ovn_cli.ovn("--may-exist", "lr-route-add", acc_router.name,
                subnet.cidr, transit_ips[0].split("/")[0])

    acc_ifaces = db.scalars(select(RouterInterface).where(RouterInterface.router_id == acc_router.id)).all()
    for iface in acc_ifaces:
        subnet = db.get(Subnet, iface.subnet_id)
        if subnet:
            ovn_cli.ovn("--may-exist", "lr-route-add", req_router.name,
                subnet.cidr, transit_ips[1].split("/")[0])

@router.get("/vpc-peerings", response_model=list[VPCPeeringOut])
def list_vpc_peerings(
    current: CurrentUser,
    db: DB,
    project_id: str | None = None,
):
    q = select(VPCPeering)
    if current.get("role") != "admin":
        project_id = _pid(current)
    if project_id:
        q = q.where(VPCPeering.project_id == project_id)
    return db.execute(q).scalars().all()

@router.get("/vpc-peerings/{peering_id}", response_model=VPCPeeringOut)
def get_vpc_peering(peering_id: str, current: CurrentUser, db: DB):
    return _own(db.get(VPCPeering, peering_id), current, "VPC peering")

@router.delete("/vpc-peerings/{peering_id}", status_code=204)
def delete_vpc_peering(peering_id: str, current: CurrentUser, db: DB):
    peering = _own(db.get(VPCPeering, peering_id), current, "VPC peering")

    # Remove OVN routes and ports
    req_router = db.get(Router, peering.requester_router_id)
    acc_router = db.get(Router, peering.accepter_router_id)
    if req_router and acc_router:
        port_prefix = f"lrp-peer-{peering.id[:8]}"
        # Remove static routes
        for iface in db.scalars(select(RouterInterface).where(RouterInterface.router_id == req_router.id)).all():
            subnet = db.get(Subnet, iface.subnet_id)
            if subnet:
                ovn_cli.ovn("--if-exists", "lr-route-del", acc_router.name, subnet.cidr)
        for iface in db.scalars(select(RouterInterface).where(RouterInterface.router_id == acc_router.id)).all():
            subnet = db.get(Subnet, iface.subnet_id)
            if subnet:
                ovn_cli.ovn("--if-exists", "lr-route-del", req_router.name, subnet.cidr)
        # Remove peer ports
        ovn_cli.ovn("--if-exists", "lrp-del", f"{port_prefix}-req")
        ovn_cli.ovn("--if-exists", "lrp-del", f"{port_prefix}-acc")

    db.delete(peering)
    db.commit()
    return None

@router.post("/bgp-peers", response_model=BGPPeerOut, status_code=201)
def create_bgp_peer(payload: BGPPeerIn, current: CurrentUser, db: DB):
    """Register a BGP peer for dynamic gateway advertisement."""
    router = require_by_ref(db, Router, payload.router_id, "router")
    project_id = _pid(current)
    _check_router(db, router, current)

    peer = BGPPeer(
        project_id=project_id,
        name=payload.name,
        router_id=router.id,
        peer_ip=payload.peer_ip,
        remote_as=payload.remote_as,
        local_as=payload.local_as,
        auth_password=payload.auth_password,
        advertised_prefixes=json.dumps(payload.advertised_prefixes or []),
        is_active=True,
        status="down",
    )
    db.add(peer)
    db.commit()
    db.refresh(peer)
    return {
        "id": peer.id,
        "project_id": peer.project_id,
        "name": peer.name,
        "router_id": peer.router_id,
        "peer_ip": peer.peer_ip,
        "remote_as": peer.remote_as,
        "local_as": peer.local_as,
        "has_password": bool(peer.auth_password),
        "advertised_prefixes": json.loads(peer.advertised_prefixes or "[]"),
        "is_active": peer.is_active,
        "status": peer.status,
        "created_at": peer.created_at,
        "updated_at": peer.updated_at,
    }

@router.get("/bgp-peers", response_model=list[BGPPeerOut])
def list_bgp_peers(
    current: CurrentUser,
    db: DB,
    router_id: str | None = None,
    project_id: str | None = None,
):
    q = select(BGPPeer)
    if current.get("role") != "admin":
        project_id = _pid(current)
    if router_id:
        q = q.where(BGPPeer.router_id == router_id)
    if project_id:
        q = q.where(BGPPeer.project_id == project_id)
    peers = db.execute(q).scalars().all()
    result = []
    for peer in peers:
        result.append(_bgp_out(peer))
    return result

@router.get("/bgp-peers/{peer_id}", response_model=BGPPeerOut)
def get_bgp_peer(peer_id: str, current: CurrentUser, db: DB):
    peer = _own(db.get(BGPPeer, peer_id), current, "BGP peer")
    return _bgp_out(peer)

@router.delete("/bgp-peers/{peer_id}", status_code=204)
def delete_bgp_peer(peer_id: str, current: CurrentUser, db: DB):
    peer = _own(db.get(BGPPeer, peer_id), current, "BGP peer")
    db.delete(peer)
    db.commit()
    return None

@router.post("/bgp-peers/{peer_id}/sync-config")
def sync_bgp_config(peer_id: str, current: CurrentUser, db: DB):
    """Generate FRR configuration snippet for a BGP peer."""
    peer = _own(db.get(BGPPeer, peer_id), current, "BGP peer")

    prefixes = json.loads(peer.advertised_prefixes or "[]")
    joined_prefixes = "\n".join(["  network " + p for p in prefixes])
    config = f"""router bgp {peer.local_as}
 neighbor {peer.peer_ip} remote-as {peer.remote_as}
{" neighbor " + peer.peer_ip + " password " + peer.auth_password if peer.auth_password else ""}
 address-family ipv4 unicast
{joined_prefixes}
 exit-address-family
"""
    return {
        "peer_id": peer_id,
        "frr_config": config,
        "note": "Apply this configuration to the FRR/BGP container manually or via network-ops automation.",
    }
