from fastapi import APIRouter, HTTPException
from shared.auth_middleware import CurrentUser
from sqlalchemy import select

from .. import ovn_cli
from ..config import METADATA_LINK_LOCAL, PROVIDER_HOST_IP_RAW
from ..db import DB
from ..ipam import subnet_ip_with_prefix
from ..models import Network, Router, RouterInterface, Subnet, mac
from ..ovn_cli import ensure_router_gateway_ovn
from ..schemas import RouterGatewayIn, RouterIn, RouterInterfaceIn
from ..serializers import get_by_ref, obj_to_dict, require_by_ref, router_to_os_dict

router = APIRouter()


@router.post("/routers", status_code=201)
def create_router(payload: RouterIn, current: CurrentUser, db: DB):
    if get_by_ref(db, Router, payload.name):
        raise HTTPException(status_code=409, detail="router already exists")
    router = Router(name=payload.name)
    ovn_cli.ovn("--may-exist", "lr-add", payload.name)
    ovn_cli.ovn("set", "logical_router", payload.name, "external_ids:cloudx=true")
    db.add(router)
    db.commit()
    db.refresh(router)
    return obj_to_dict(router)

@router.get("/routers")
def list_routers(current: CurrentUser, db: DB):
    return {"items": [router_to_os_dict(db, r, current) for r in db.scalars(select(Router).order_by(Router.created_at)).all()]}

@router.get("/routers/{ref}")
def show_router(ref: str, current: CurrentUser, db: DB):
    return router_to_os_dict(db, require_by_ref(db, Router, ref, "router"), current)

@router.delete("/routers/{ref}")
def delete_router(ref: str, _: CurrentUser, db: DB):
    router = require_by_ref(db, Router, ref, "router")

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
    return {"status": "deleted", "id": router.id, "name": router.name}

@router.post("/routers/{ref}/gateway")
def set_router_gateway(ref: str, payload: RouterGatewayIn, _: CurrentUser, db: DB):
    network_ref = payload.resolved_network
    if not network_ref:
        raise HTTPException(status_code=422, detail="'external_network' field is required")
    router = require_by_ref(db, Router, ref, "router")
    network = require_by_ref(db, Network, network_ref, "network")
    if not network.external:
        raise HTTPException(status_code=400, detail="router gateway requires an external network")
    ensure_router_gateway_ovn(router, network)
    router.external_network_id = network.id
    db.commit()
    db.refresh(router)
    ovn_cli.ovn("set", "logical_router", router.name, f"external_ids:external_network={network.name}")
    return {"router": router.name, "external_network": network.name, "status": "ACTIVE"}

@router.post("/routers/{ref}/interfaces", status_code=201)
def add_router_interface(ref: str, payload: RouterInterfaceIn, _: CurrentUser, db: DB):
    router = require_by_ref(db, Router, ref, "router")
    subnet = require_by_ref(db, Subnet, payload.subnet, "subnet")
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
    # Add OVN static route for metadata service (169.254.169.254 → host)
    # Requires PROVIDER_HOST_IP to be set so OVN knows the nexthop.
    # On the host, iptables redirects :80 to metadata-proxy (default :8776).
    if PROVIDER_HOST_IP_RAW:
        ovn_cli.ovn("--may-exist", "lr-route-add", router.name,
            f"{METADATA_LINK_LOCAL}/32", PROVIDER_HOST_IP_RAW, check=False)
        print(f"metadata route: {METADATA_LINK_LOCAL}/32 → {PROVIDER_HOST_IP_RAW} on {router.name}", flush=True)
    return obj_to_dict(iface)

@router.delete("/routers/{ref}/interfaces/{subnet_ref}")
def remove_router_interface(ref: str, subnet_ref: str, _: CurrentUser, db: DB):
    router = require_by_ref(db, Router, ref, "router")
    subnet = require_by_ref(db, Subnet, subnet_ref, "subnet")
    iface = db.scalar(select(RouterInterface).where(RouterInterface.router_id == router.id, RouterInterface.subnet_id == subnet.id))
    if not iface:
        raise HTTPException(status_code=404, detail="router interface not found")
    network = db.get(Network, subnet.network_id)
    ovn_cli.ovn("--if-exists", "lsp-del", iface.switch_port_name)
    ovn_cli.ovn("--if-exists", "lrp-del", iface.router_port_name)
    db.delete(iface)
    db.commit()
    return {"status": "deleted", "router": router.name, "subnet": subnet.name, "network": network.name}
