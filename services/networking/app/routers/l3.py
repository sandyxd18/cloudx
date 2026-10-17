import ipaddress
import json

from fastapi import APIRouter, HTTPException
from shared.auth_middleware import CurrentUser
from sqlalchemy import select

from .. import ovn_cli
from ..config import METADATA_LINK_LOCAL, PROVIDER_HOST_IP_RAW
from ..access import require_owned, scope
from ..db import DB
from ..quota import check_quota
from ..events import _actor, _publish_net_event
from ..ipam import subnet_ip_with_prefix
from ..models import Network, Router, RouterInterface, Subnet, mac
from ..ovn_cli import ensure_router_gateway_ovn
from ..schemas import RouterGatewayIn, RouterIn, RouterInterfaceIn, RouterUpdate
from ..serializers import get_by_ref, obj_to_dict, router_to_os_dict

router = APIRouter()


def _router_external_ip(db, network: Network) -> str:
    # ponytail: gateway_ip is the upstream next-hop, so the router takes the address just below
    # the subnet's allocation pool; with no pool, falls back to gateway_ip (legacy, no default route).
    sub = db.scalar(select(Subnet).where(Subnet.network_id == network.id))
    if sub and sub.allocation_start:
        return str(ipaddress.ip_address(sub.allocation_start) - 1)
    return network.gateway_ip


def _snat(router_name: str, router_ip: str, port_ip: str) -> None:
    cidr = str(ipaddress.ip_interface(port_ip).network)
    ovn_cli.ovn("--may-exist", "lr-nat-add", router_name, "snat", router_ip, cidr, check=False)


@router.post("/routers", status_code=201)
def create_router(payload: RouterIn, current: CurrentUser, db: DB):
    if get_by_ref(db, Router, payload.name, current=current):
        raise HTTPException(status_code=409, detail="router already exists")
    check_quota(db, current.get("project_id"), Router, "routers")
    router = Router(name=payload.name, project_id=current.get("project_id"))
    ovn_cli.ovn("--may-exist", "lr-add", payload.name)
    ovn_cli.ovn("set", "logical_router", payload.name, "external_ids:cloudx=true")
    db.add(router)
    db.commit()
    db.refresh(router)
    _publish_net_event("network.router.create", {"router_id": router.id, "name": router.name, **_actor(current)})
    return obj_to_dict(router)

@router.patch("/routers/{ref}")
@router.put("/routers/{ref}")
def update_router(ref: str, payload: RouterUpdate, current: CurrentUser, db: DB):
    router = require_owned(current, db, Router, ref, "router")
    if payload.routes is not None:
        nets = [ipaddress.ip_network(s.cidr, strict=False) for s in db.scalars(
            select(Subnet).join(RouterInterface, RouterInterface.subnet_id == Subnet.id)
            .where(RouterInterface.router_id == router.id))]
        gw = db.get(Network, router.external_network_id) if router.external_network_id else None
        if gw and gw.provider_cidr:
            nets.append(ipaddress.ip_network(gw.provider_cidr, strict=False))
        for r in payload.routes:  # like Neutron: the next hop must be directly reachable from the router
            if not any(ipaddress.ip_address(r.nexthop) in n for n in nets):
                raise HTTPException(status_code=400, detail=f"nexthop {r.nexthop} is not in a subnet connected to the router")
        old = {(r["destination"], r["nexthop"]) for r in json.loads(router.routes or "[]")}
        new = {(r.destination, r.nexthop) for r in payload.routes}
        for dest, hop in old - new:
            ovn_cli.ovn("--if-exists", "lr-route-del", router.name, dest, hop)
        for dest, hop in new - old:
            ovn_cli.ovn("--may-exist", "lr-route-add", router.name, dest, hop)
        router.routes = json.dumps([r.model_dump() for r in payload.routes]) if payload.routes else None
    db.commit()
    db.refresh(router)
    return router_to_os_dict(db, router, current)


@router.get("/routers")
def list_routers(current: CurrentUser, db: DB):
    return {"items": [router_to_os_dict(db, r, current) for r in db.scalars(scope(select(Router), Router, current).order_by(Router.created_at)).all()]}

@router.get("/routers/{ref}")
def show_router(ref: str, current: CurrentUser, db: DB):
    return router_to_os_dict(db, require_owned(current, db, Router, ref, "router"), current)

@router.delete("/routers/{ref}")
def delete_router(ref: str, current: CurrentUser, db: DB):
    router = require_owned(current, db, Router, ref, "router")

    # ── 1. Remove all router interfaces (lrp + lsp) from OVN and DB ──────────
    interfaces = db.scalars(
        select(RouterInterface).where(RouterInterface.router_id == router.id)
    ).all()
    for iface in interfaces:
        ovn_cli.ovn("--if-exists", "lsp-del", iface.switch_port_name)
        ovn_cli.ovn("--if-exists", "lrp-del", iface.router_port_name)
        db.delete(iface)
    db.flush()  # apply interface deletes before router delete

    # ── 2. Remove the logical router from OVN (also removes its gateway port) ─
    ovn_cli.ovn("--if-exists", "lr-del", router.name)

    # ── 3. Delete router record ───────────────────────────────────────────────
    db.delete(router)
    db.commit()
    _publish_net_event("network.router.delete", {"router_id": router.id, "name": router.name, **_actor(current)})
    return {"status": "deleted", "id": router.id, "name": router.name}

@router.post("/routers/{ref}/gateway")
def set_router_gateway(ref: str, payload: RouterGatewayIn, current: CurrentUser, db: DB):
    network_ref = payload.resolved_network
    if not network_ref:
        raise HTTPException(status_code=422, detail="'external_network' field is required")
    router = require_owned(current, db, Router, ref, "router")
    network = require_owned(current, db, Network, network_ref, "network")
    if not network.external:
        raise HTTPException(status_code=400, detail="router gateway requires an external network")
    router_ip = _router_external_ip(db, network)
    ensure_router_gateway_ovn(router, network, router_ip)
    router.external_network_id = network.id
    db.commit()
    db.refresh(router)
    for i in db.scalars(select(RouterInterface).where(RouterInterface.router_id == router.id)).all():
        _snat(router.name, router_ip, i.ip_address)
    ovn_cli.ovn("set", "logical_router", router.name, f"external_ids:external_network={network.name}")
    return {"router": router.name, "external_network": network.name, "status": "ACTIVE"}

@router.post("/routers/{ref}/interfaces", status_code=201)
def add_router_interface(ref: str, payload: RouterInterfaceIn, current: CurrentUser, db: DB):
    router = require_owned(current, db, Router, ref, "router")
    subnet = require_owned(current, db, Subnet, payload.subnet, "subnet")
    network = db.get(Network, subnet.network_id)
    existing = db.scalar(select(RouterInterface).where(RouterInterface.router_id == router.id, RouterInterface.subnet_id == subnet.id))
    if existing:
        raise HTTPException(status_code=409, detail="router interface already exists")
    lrp = f"lrp-{router.name}-{subnet.name}"
    lsp = f"lsp-{router.name}-{subnet.name}"
    port_mac = mac()
    port_ip = subnet_ip_with_prefix(subnet)
    ovn_cli.ovn("--may-exist", "lrp-add", router.name, lrp, port_mac, port_ip)
    ovn_cli.ovn("set", "logical_router_port", lrp, "external_ids:cloudx=true")
    ovn_cli.ovn("--may-exist", "lsp-add", network.name, lsp)
    ovn_cli.ovn("set", "logical_switch_port", lsp, "external_ids:cloudx=true")
    ovn_cli.ovn("lsp-set-type", lsp, "router")
    ovn_cli.ovn("lsp-set-addresses", lsp, "router")
    ovn_cli.ovn("lsp-set-options", lsp, f"router-port={lrp}")
    iface = RouterInterface(router_id=router.id, subnet_id=subnet.id, router_port_name=lrp, switch_port_name=lsp, mac_address=port_mac, ip_address=port_ip)
    db.add(iface)
    db.commit()
    db.refresh(iface)
    from .routing import sync_peering_routes
    sync_peering_routes(db, router)
    if router.external_network_id:
        _snat(router.name, _router_external_ip(db, db.get(Network, router.external_network_id)), port_ip)
    # Add OVN static route for metadata service (169.254.169.254 → host)
    # Requires PROVIDER_HOST_IP to be set so OVN knows the nexthop.
    # On the host, iptables redirects :80 to metadata-proxy (default :8776).
    if PROVIDER_HOST_IP_RAW:
        ovn_cli.ovn("--may-exist", "lr-route-add", router.name,
            f"{METADATA_LINK_LOCAL}/32", PROVIDER_HOST_IP_RAW, check=False)
        print(f"metadata route: {METADATA_LINK_LOCAL}/32 → {PROVIDER_HOST_IP_RAW} on {router.name}", flush=True)
    return obj_to_dict(iface)

@router.delete("/routers/{ref}/interfaces/{subnet_ref}")
def remove_router_interface(ref: str, subnet_ref: str, current: CurrentUser, db: DB):
    router = require_owned(current, db, Router, ref, "router")
    subnet = require_owned(current, db, Subnet, subnet_ref, "subnet")
    iface = db.scalar(select(RouterInterface).where(RouterInterface.router_id == router.id, RouterInterface.subnet_id == subnet.id))
    if not iface:
        raise HTTPException(status_code=404, detail="router interface not found")
    network = db.get(Network, subnet.network_id)
    # Remove peering routes for this subnet on peered routers
    from .routing import delete_peering_routes_for_subnet
    delete_peering_routes_for_subnet(db, router, subnet)
    ovn_cli.ovn("--if-exists", "lsp-del", iface.switch_port_name)
    ovn_cli.ovn("--if-exists", "lrp-del", iface.router_port_name)
    db.delete(iface)
    db.commit()
    return {"status": "deleted", "router": router.name, "subnet": subnet.name, "network": network.name}
