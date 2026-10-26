from __future__ import annotations
import ipaddress
import json

from fastapi import APIRouter
from shared.auth_middleware import InternalAuth
from sqlalchemy import select

from .. import ovn_cli
from ..db import DB
from ..models import BGPPeer, BGPSpeaker, Network, Router

router = APIRouter()


@router.get("/internal/bgp-agent/{host}", dependencies=[InternalAuth])
def get_bgp_agent_config(host: str, db: DB):
    """Return BGP speaker instances and active peers assigned to this compute/storage host."""
    speakers = db.scalars(select(BGPSpeaker).where(BGPSpeaker.host == host)).all()
    speakers_out = []

    for spk in speakers:
        rtr = db.get(Router, spk.router_id)
        if not rtr:
            continue
        ext_net = db.get(Network, spk.outer_network_id)
        if not ext_net:
            continue

        # router_ext_ip from external network subnet allocation_start or gateway_ip
        from .l3 import _router_external_ip
        router_ext_ip = _router_external_ip(db, ext_net)

        # outer_prefixlen from ext_net provider_cidr
        outer_prefixlen = 24
        if ext_net.provider_cidr:
            try:
                outer_prefixlen = ipaddress.ip_network(ext_net.provider_cidr, strict=False).prefixlen
            except Exception: # best-effort: fallback to default prefixlen if provider_cidr parsing fails
                pass

        # outer_gateway_ip from ext_net gateway_ip
        outer_gateway_ip = ext_net.gateway_ip or "172.24.4.1"

        # local_subnets from router interfaces
        from .routing import _router_subnets
        local_subnets = [str(n) for n in _router_subnets(db, rtr.id)]

        peers = db.scalars(
            select(BGPPeer).where(
                (BGPPeer.router_id == rtr.id) & (BGPPeer.is_active == True)
            )
        ).all()

        peers_out = []
        for p in peers:
            peers_out.append({
                "id": p.id,
                "name": p.name,
                "peer_ip": p.peer_ip,
                "remote_as": p.remote_as,
                "local_as": p.local_as,
                "auth_password": p.auth_password,
                "advertised_prefixes": json.loads(p.advertised_prefixes or "[]"),
            })

        short = spk.id.split("-", 1)[1][:8] if "-" in spk.id else spk.id[:8]
        speakers_out.append({
            "id": spk.id,
            "router_id": spk.router_id,
            "short": short,
            "outer_lsp": f"lsp-bgp-{short}-out",
            "outer_mac": spk.outer_mac,
            "outer_ip": spk.outer_ip,
            "outer_prefixlen": outer_prefixlen,
            "outer_gateway_ip": outer_gateway_ip,
            "router_ext_ip": router_ext_ip,
            "local_subnets": local_subnets,
            "peers": peers_out,
        })

    return {"speakers": speakers_out}


@router.post("/internal/bgp-agent/{host}/status", dependencies=[InternalAuth])
def update_bgp_agent_status(host: str, payload: dict, db: DB):
    """Reconcile BGP peer statuses and learned routes from agent report."""
    peers_status = payload.get("peers", {})
    speakers = db.scalars(select(BGPSpeaker).where(BGPSpeaker.host == host)).all()
    speaker_router_ids = {spk.router_id for spk in speakers}

    from ..models import BGPLearnedRoute

    for peer_id, st in peers_status.items():
        peer = db.get(BGPPeer, peer_id)
        if not peer or peer.router_id not in speaker_router_ids:
            continue

        rtr = db.get(Router, peer.router_id)
        if not rtr:
            continue

        new_status = st.get("status", "down")
        status_reason = st.get("status_reason")
        routes = st.get("routes", [])

        peer.status = new_status
        peer.status_reason = status_reason

        # Existing learned routes in DB for this peer
        existing_routes = db.scalars(select(BGPLearnedRoute).where(BGPLearnedRoute.peer_id == peer.id)).all()
        existing_map = {r.prefix: r for r in existing_routes}

        # Gather local subnets and router external/transit interfaces for safety checks
        router_nets = []
        from .routing import _router_subnets
        router_nets.extend(_router_subnets(db, rtr.id))
        ext_net = db.get(Network, rtr.external_network_id) if rtr.external_network_id else None
        if ext_net and ext_net.provider_cidr:
            try:
                router_nets.append(ipaddress.ip_network(ext_net.provider_cidr, strict=False))
            except Exception: # best-effort: fallback if provider_cidr parsing fails
                pass

        # Static routes configured on router
        static_destinations = set()
        if rtr.routes:
            try:
                for sr in json.loads(rtr.routes):
                    if isinstance(sr, dict) and "destination" in sr:
                        static_destinations.add(sr["destination"])
            except Exception: # best-effort: fallback if routes json parsing fails
                pass

        # If status != established, pull ALL learned routes for this peer
        if new_status != "established":
            for prefix, r_obj in existing_map.items():
                ovn_cli.ovn("--if-exists", "lr-route-del", rtr.name, prefix, r_obj.nexthop, check=False)
                db.delete(r_obj)
            db.commit()
            continue

        # If established, reconcile routes
        reported_map = {}
        for rt in routes:
            pfx = rt.get("prefix")
            nh = rt.get("nexthop")
            if not pfx or not nh:
                continue
            try:
                pfx_net = ipaddress.ip_network(pfx, strict=False)
                nh_ip = ipaddress.ip_address(nh)
            except Exception:  # best-effort: unparsable nexthop/prefix is skipped
                continue

            # ABAIKAN: default 0.0.0.0/0
            if pfx_net.prefixlen == 0 or pfx == "0.0.0.0/0":
                continue

            # IGNORE: overlap with a router subnet (equal, more specific, or a supernet)
            overlaps = False
            for rnet in router_nets:
                if pfx_net.subnet_of(rnet) or rnet.subnet_of(pfx_net) or pfx_net == rnet:
                    overlaps = True
                    break
            if overlaps:
                continue

            # ABAIKAN: nexthop di luar subnet jaringan eksternal router
            if ext_net and ext_net.provider_cidr:
                try:
                    ext_net_obj = ipaddress.ip_network(ext_net.provider_cidr, strict=False)
                    if nh_ip not in ext_net_obj:
                        continue
                except Exception:  # best-effort: unparsable external CIDR, nexthop check skipped
                    pass

            # IGNORE: prefix that is already a non-BGP static route on the router
            if pfx in static_destinations:
                continue

            reported_map[pfx] = nh

        # Add new or updated routes
        for pfx, nh in reported_map.items():
            if pfx not in existing_map:
                # Add via OVN
                ovn_cli.ovn("--may-exist", "lr-route-add", rtr.name, pfx, nh, check=False)
                new_r = BGPLearnedRoute(peer_id=peer.id, prefix=pfx, nexthop=nh)
                db.add(new_r)
            else:
                existing_r = existing_map[pfx]
                if existing_r.nexthop != nh:
                    ovn_cli.ovn("--if-exists", "lr-route-del", rtr.name, pfx, existing_r.nexthop, check=False)
                    ovn_cli.ovn("--may-exist", "lr-route-add", rtr.name, pfx, nh, check=False)
                    existing_r.nexthop = nh

        # Remove missing/withdrawn routes
        for pfx, r_obj in existing_map.items():
            if pfx not in reported_map:
                ovn_cli.ovn("--if-exists", "lr-route-del", rtr.name, pfx, r_obj.nexthop, check=False)
                db.delete(r_obj)

        db.commit()

    return {"status": "ok"}
