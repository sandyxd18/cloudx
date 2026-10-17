import json
import ipaddress

from fastapi import APIRouter, HTTPException
from shared.auth_middleware import CurrentUser
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import ovn_cli
from ..db import DB
from ..models import BGPPeer, BGPSpeaker, BGPLearnedRoute, Network, Router, RouterInterface, Subnet, VPCPeering, stable_mac
from ..schemas import BGPPeerIn, BGPPeerOut, VPCPeeringIn, VPCPeeringOut
from ..serializers import get_by_ref, require_by_ref
from shared.bird_cfg import render

router = APIRouter()


def _pid(current: dict) -> str:
    return current.get("project_id", current.get("tenant_id", "default"))


def _own(obj, current: dict, label: str):
    """404 (not 403) so ids of other projects are not leaked; admin is unrestricted."""
    if not obj or (current.get("role") != "admin" and obj.project_id != _pid(current)):
        raise HTTPException(status_code=404, detail=f"{label} not found")
    return obj


def _bgp_out(peer: BGPPeer, db: Session | None = None) -> dict:
    d = {k: v for k, v in peer.__dict__.items() if k not in ("_sa_instance_state", "auth_password")}
    outer_ip = None
    learned_routes = []
    if db:
        if peer.speaker_id:
            spk = db.get(BGPSpeaker, peer.speaker_id)
            if spk:
                outer_ip = spk.outer_ip
        routes = db.scalars(select(BGPLearnedRoute).where(BGPLearnedRoute.peer_id == peer.id)).all()
        learned_routes = [{"prefix": r.prefix, "nexthop": r.nexthop} for r in routes]

    return {
        **d,
        "has_password": bool(peer.auth_password),
        "advertised_prefixes": json.loads(peer.advertised_prefixes or "[]"),
        "speaker_id": getattr(peer, "speaker_id", None),
        "status_reason": getattr(peer, "status_reason", None),
        "outer_ip": outer_ip,
        "learned_routes": learned_routes,
    }


def infer_router_project_id(db: Session, rtr: Router) -> str | None:
    if rtr.project_id is not None:
        return rtr.project_id
    return db.scalar(
        select(Subnet.project_id)
        .join(RouterInterface, RouterInterface.subnet_id == Subnet.id)
        .where(RouterInterface.router_id == rtr.id, Subnet.project_id.isnot(None))
    )


def _check_router(db: Session, rtr: Router, current: dict) -> None:
    if not rtr:
        raise HTTPException(status_code=404, detail="router not found")
    if current.get("role") == "admin":
        return
    caller_pid = _pid(current)
    rtr_pid = infer_router_project_id(db, rtr)
    if rtr_pid is not None and rtr_pid != caller_pid:
        raise HTTPException(status_code=404, detail="router not found")


def _router_subnets(db: Session, router_id: str) -> list[ipaddress.IPv4Network]:
    ifaces = db.scalars(select(RouterInterface).where(RouterInterface.router_id == router_id)).all()
    nets = []
    for iface in ifaces:
        sub = db.get(Subnet, iface.subnet_id)
        if sub and sub.cidr:
            try:
                nets.append(ipaddress.ip_network(sub.cidr, strict=False))
            except ValueError:
                pass
    return nets


def allocate_transit_cidr(db: Session) -> str:
    used = set()
    peerings = db.scalars(select(VPCPeering)).all()
    for p in peerings:
        if p.transit_cidr:
            used.add(p.transit_cidr)
        else:
            used.add("100.100.0.0/30")  # legacy default
    for net in ipaddress.ip_network("100.100.0.0/16").subnets(new_prefix=30):
        s = str(net)
        if s not in used:
            return s
    raise HTTPException(status_code=409, detail="transit CIDR pool exhausted")


def sync_peering_routes(db: Session, router: Router) -> None:
    """Idempotent: for each active peering involving router, add routes on the OTHER router for each subnet of router via transit IP."""
    peerings = db.scalars(select(VPCPeering).where(
        ((VPCPeering.requester_router_id == router.id) | (VPCPeering.accepter_router_id == router.id))
        & (VPCPeering.status == "active")
    )).all()

    for p in peerings:
        is_req = (p.requester_router_id == router.id)
        other_id = p.accepter_router_id if is_req else p.requester_router_id
        other_router = db.get(Router, other_id)
        if not other_router:
            continue
        transit = p.transit_cidr or "100.100.0.0/30"
        net_obj = ipaddress.ip_network(transit)
        hosts = list(net_obj.hosts())
        req_ip = str(hosts[0])
        acc_ip = str(hosts[1])
        my_transit_ip = req_ip if is_req else acc_ip

        r_subnets = _router_subnets(db, router.id)
        for sub in r_subnets:
            ovn_cli.ovn("--may-exist", "lr-route-add", other_router.name, str(sub), my_transit_ip)


def delete_peering_routes_for_subnet(db: Session, router: Router, subnet: Subnet) -> None:
    peerings = db.scalars(select(VPCPeering).where(
        ((VPCPeering.requester_router_id == router.id) | (VPCPeering.accepter_router_id == router.id))
        & (VPCPeering.status == "active")
    )).all()

    for p in peerings:
        is_req = (p.requester_router_id == router.id)
        other_id = p.accepter_router_id if is_req else p.requester_router_id
        other_router = db.get(Router, other_id)
        if not other_router:
            continue
        if subnet and subnet.cidr:
            ovn_cli.ovn("--if-exists", "lr-route-del", other_router.name, subnet.cidr)


@router.post("/routers/{ref}/peering", response_model=VPCPeeringOut, status_code=201)
def create_vpc_peering(ref: str, payload: VPCPeeringIn, current: CurrentUser, db: DB):
    """Create a VPC peering between two routers."""
    requester = require_by_ref(db, Router, ref, "router", current=current)
    accepter = require_by_ref(db, Router, payload.accepter_router_id, "router", current=current)
    project_id = _pid(current)
    _check_router(db, requester, current)
    _check_router(db, accepter, current)
    if requester.id == accepter.id:
        raise HTTPException(status_code=400, detail="cannot peer a router with itself")
    if get_by_ref(db, VPCPeering, payload.name, current=current):
        raise HTTPException(status_code=409, detail="VPC peering already exists")
    dup = db.scalar(select(VPCPeering).where(
        ((VPCPeering.requester_router_id == requester.id) & (VPCPeering.accepter_router_id == accepter.id))
        | ((VPCPeering.requester_router_id == accepter.id) & (VPCPeering.accepter_router_id == requester.id))
    ))
    if dup:
        raise HTTPException(status_code=409, detail="VPC peering already exists")

    # Overlapping CIDRs check
    req_subnets = _router_subnets(db, requester.id)
    acc_subnets = _router_subnets(db, accepter.id)
    for r_net in req_subnets:
        for a_net in acc_subnets:
            if r_net.overlaps(a_net):
                raise HTTPException(status_code=409, detail=f"overlapping subnets {r_net} and {a_net}")

    transit_cidr = allocate_transit_cidr(db)

    peering = VPCPeering(
        name=payload.name,
        project_id=project_id,
        requester_router_id=requester.id,
        accepter_router_id=accepter.id,
        requester_router_name=requester.name,
        accepter_router_name=accepter.name,
        status="active",
        transit_cidr=transit_cidr,
    )
    db.add(peering)
    db.flush()  # assigns the id used for OVN port names; not committed until OVN succeeds

    port_prefix = f"lrp-peer-{peering.id[:8]}"
    try:
        _apply_peering_ovn(db, peering)
    except Exception:
        db.rollback()
        # best-effort: cleanup OVN ports if creation fails midway
        for pname in (f"{port_prefix}-req", f"{port_prefix}-acc"):
            try:
                ovn_cli.ovn("--if-exists", "lrp-del", pname)
            except Exception: # best-effort: ignore port deletion failure during rollback
                pass
        raise
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

    transit_cidr = peering.transit_cidr or "100.100.0.0/30"
    net_obj = ipaddress.ip_network(transit_cidr)
    hosts = list(net_obj.hosts())
    req_ip = str(hosts[0])
    acc_ip = str(hosts[1])

    # Create router ports on both routers with peer=
    ovn_cli.ovn("--may-exist", "lrp-add", req_router.name, req_port_name, req_mac, f"{req_ip}/{net_obj.prefixlen}", f"peer={acc_port_name}")
    ovn_cli.ovn("--may-exist", "lrp-add", acc_router.name, acc_port_name, acc_mac, f"{acc_ip}/{net_obj.prefixlen}", f"peer={req_port_name}")

    # Sync routes for both directions
    sync_peering_routes(db, req_router)
    sync_peering_routes(db, acc_router)

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
    router = require_by_ref(db, Router, payload.router_id, "router", current=current)
    project_id = _pid(current)
    _check_router(db, router, current)

    if not router.external_network_id:
        raise HTTPException(status_code=400, detail="router must have an external gateway to configure BGP")

    # Check local_as consistency across peers on the same router
    existing_peers = db.scalars(select(BGPPeer).where(BGPPeer.router_id == router.id)).all()
    for ep in existing_peers:
        if ep.local_as != payload.local_as:
            raise HTTPException(status_code=409, detail=f"local_as must match existing peers on this router (expected {ep.local_as})")

    if get_by_ref(db, BGPPeer, payload.name, current=current):
        raise HTTPException(status_code=409, detail="BGP peer already exists")

    # Find or allocate BGPSpeaker for this router
    speaker = db.scalars(select(BGPSpeaker).where(BGPSpeaker.router_id == router.id)).first()
    ext_net = db.get(Network, router.external_network_id)
    if not ext_net:
        raise HTTPException(status_code=400, detail="router external network not found")

    from .vpn import _get_router_host, _allocate_outer_ip

    host = _get_router_host(router.name)

    if not speaker:
        outer_ip = _allocate_outer_ip(db, ext_net)
        speaker = BGPSpeaker(
            router_id=router.id,
            project_id=project_id,
            host=host,
            outer_network_id=ext_net.id,
            outer_ip=outer_ip,
            outer_mac=stable_mac(f"bgpsp-{router.id}"),
            status="down",
        )
        db.add(speaker)
        db.flush()

    short = speaker.id.split("-", 1)[1][:8] if "-" in speaker.id else speaker.id[:8]
    lsp_out_name = f"lsp-bgp-{short}-out"

    try:
        ovn_cli.ovn("--may-exist", "lsp-add", ext_net.name, lsp_out_name)
        ovn_cli.ovn("set", "logical_switch_port", lsp_out_name, "external_ids:cloudx=true", f"external_ids:bgp_speaker={speaker.id}")
        ovn_cli.ovn("lsp-set-addresses", lsp_out_name, f"{speaker.outer_mac} {speaker.outer_ip}")
    except Exception as exc: # best-effort: rollback speaker LSP on OVN failure
        ovn_cli.ovn("--if-exists", "lsp-del", lsp_out_name, check=False)
        if not existing_peers:
            db.delete(speaker)
        db.commit()
        raise HTTPException(status_code=500, detail=f"failed to provision BGP speaker LSP in OVN: {exc}")

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
        speaker_id=speaker.id,
    )
    db.add(peer)
    db.commit()
    db.refresh(peer)
    return _bgp_out(peer, db)


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
        result.append(_bgp_out(peer, db))
    return result


@router.get("/bgp-peers/{peer_id}", response_model=BGPPeerOut)
def get_bgp_peer(peer_id: str, current: CurrentUser, db: DB):
    peer = _own(db.get(BGPPeer, peer_id), current, "BGP peer")
    return _bgp_out(peer, db)


@router.delete("/bgp-peers/{peer_id}", status_code=204)
def delete_bgp_peer(peer_id: str, current: CurrentUser, db: DB):
    peer = _own(db.get(BGPPeer, peer_id), current, "BGP peer")
    rtr = db.get(Router, peer.router_id)

    # 1. Pull all learned routes owned by this peer from OVN and delete rows
    learned_routes = db.scalars(select(BGPLearnedRoute).where(BGPLearnedRoute.peer_id == peer.id)).all()
    for lr in learned_routes:
        if rtr:
            ovn_cli.ovn("--if-exists", "lr-route-del", rtr.name, lr.prefix, lr.nexthop, check=False)
        db.delete(lr)

    speaker_id = peer.speaker_id
    db.delete(peer)
    db.flush()

    # 2. Check if this was the last peer on the router/speaker
    if speaker_id:
        remaining_peers = db.scalars(select(BGPPeer).where(BGPPeer.speaker_id == speaker_id)).all()
        if not remaining_peers:
            speaker = db.get(BGPSpeaker, speaker_id)
            if speaker:
                short = speaker.id.split("-", 1)[1][:8] if "-" in speaker.id else speaker.id[:8]
                lsp_out_name = f"lsp-bgp-{short}-out"
                ext_net = db.get(Network, speaker.outer_network_id)
                if ext_net:
                    ovn_cli.ovn("--if-exists", "lsp-del", lsp_out_name, check=False)
                db.delete(speaker)

    db.commit()
    return None


@router.post("/bgp-peers/{peer_id}/sync-config")
def sync_bgp_config(peer_id: str, current: CurrentUser, db: DB):
    """Generate BIRD configuration snippet for a BGP peer."""
    peer = _own(db.get(BGPPeer, peer_id), current, "BGP peer")
    rtr = db.get(Router, peer.router_id)
    speaker = db.get(BGPSpeaker, peer.speaker_id) if peer.speaker_id else None

    if not speaker or not rtr:
        raise HTTPException(status_code=409, detail="BGP speaker not allocated for this router yet")

    ext_net = db.get(Network, speaker.outer_network_id)
    if not ext_net:
        raise HTTPException(status_code=409, detail="external network not found")

    from .l3 import _router_external_ip
    router_ext_ip = _router_external_ip(db, ext_net)
    local_subnets = [str(n) for n in _router_subnets(db, rtr.id)]

    peer_dict = {
        "id": peer.id,
        "name": peer.name,
        "peer_ip": peer.peer_ip,
        "remote_as": peer.remote_as,
        "local_as": peer.local_as,
        "auth_password": peer.auth_password,
        "advertised_prefixes": json.loads(peer.advertised_prefixes or "[]"),
        "is_active": peer.is_active,
    }

    speaker_dict = {
        "outer_ip": speaker.outer_ip,
        "router_ext_ip": router_ext_ip,
        "local_subnets": local_subnets,
    }

    bird_cfg = render(speaker_dict, [peer_dict], mask_password=True)

    return {
        "peer_id": peer_id,
        "bird_config": bird_cfg,
        "frr_config": bird_cfg,
        "note": "BIRD 2 configuration generated by cloudx shared.bird_cfg.render",
    }
