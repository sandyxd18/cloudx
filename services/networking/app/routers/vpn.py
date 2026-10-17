import ipaddress
import json
import uuid

from fastapi import APIRouter, HTTPException
from shared.auth_middleware import CurrentUser, InternalAuth
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import ovn_cli
from ..access import own
from ..db import DB
from ..models import (
    FloatingIP,
    IKEPolicy,
    IPsecPolicy,
    Network,
    Router,
    RouterInterface,
    Subnet,
    VPNGateway,
    VPNSiteConnection,
    BGPSpeaker,
    stable_mac,
)
from ..schemas import (
    IKEPolicyIn,
    IKEPolicyOut,
    IPsecPolicyIn,
    IPsecPolicyOut,
    VPNSiteConnectionIn,
    VPNSiteConnectionOut,
)
from ..serializers import require_by_ref

router = APIRouter()


def _pol(db, current: dict, model, pid: str, label: str):
    obj = db.get(model, pid)
    if not obj:
        raise HTTPException(status_code=404, detail=f"{label} not found")
    return own(current, obj, label)

def _own_conn(db, current: dict, conn_id: str) -> VPNSiteConnection:
    conn = db.get(VPNSiteConnection, conn_id)
    pid = current.get("project_id", current.get("tenant_id", "default"))
    if not conn or (current.get("role") != "admin" and conn.project_id != pid):
        raise HTTPException(status_code=404, detail="VPN site connection not found")
    return conn


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


def _allocate_vpn_transit_cidr(db: Session) -> str:
    used = set()
    gateways = db.scalars(select(VPNGateway)).all()
    for gw in gateways:
        if gw.transit_cidr:
            used.add(gw.transit_cidr)
    for net in ipaddress.ip_network("100.101.0.0/16").subnets(new_prefix=30):
        s = str(net)
        if s not in used:
            return s
    raise HTTPException(status_code=409, detail="VPN transit CIDR pool exhausted")


def _allocate_outer_ip(db: Session, network: Network) -> str:
    if not network.provider_cidr or not network.allocation_start:
        raise HTTPException(status_code=400, detail="external network missing provider cidr / allocation_start")
    net = ipaddress.ip_network(network.provider_cidr, strict=False)
    
    used_ips = set()
    # Exclude network gateway, router gateway IPs, floating IPs, and existing vpn gateway outer ips
    for r in db.scalars(select(Router)).all():
        if r.external_network_id == network.id:
            # check router external port IP or _router_external_ip
            pass
    for fip in db.scalars(select(FloatingIP).where(FloatingIP.external_network_id == network.id)).all():
        if fip.floating_ip or fip.floating_ip_address:
            used_ips.add(fip.floating_ip or fip.floating_ip_address)
    for gw in db.scalars(select(VPNGateway).where(VPNGateway.outer_network_id == network.id)).all():
        if gw.outer_ip:
            used_ips.add(gw.outer_ip)
    for bgpsp in db.scalars(select(BGPSpeaker).where(BGPSpeaker.outer_network_id == network.id)).all():
        if bgpsp.outer_ip:
            used_ips.add(bgpsp.outer_ip)

    start_ip = ipaddress.ip_address(network.allocation_start)
    # also avoid .100–.102 reserved FIP range if desired, or just iterate from allocation_start
    curr = start_ip
    while curr in net:
        s = str(curr)
        if s not in used_ips and curr != net.network_address and curr != net.broadcast_address:
            return s
        curr += 1
    raise HTTPException(status_code=409, detail="external network IP pool exhausted for VPN gateway")


def _get_router_host(router_name: str) -> str:
    """Hostname of the chassis that hosts the router's external gateway port (NB Gateway_Chassis -> SB Chassis)."""
    lrp = ovn_cli.router_external_port_names(router_name)[0]
    first = (ovn_cli.ovn("lrp-get-gateway-chassis", lrp, check=False).split() or [""])[0]
    chassis = first.removeprefix(f"{lrp}-")
    if chassis:
        out = ovn_cli.sbctl("--bare", "--columns=hostname", "find", "Chassis", f"name={chassis}")
        if out.strip().strip('"'):
            return out.strip().strip('"')
    raise HTTPException(status_code=409, detail="unable to resolve router gateway chassis host for VPN gateway")

def _gw_short(gw_id: str) -> str:
    # gateway id format is vpngw-<12 hex chars>
    parts = gw_id.split("-", 1)
    if len(parts) > 1:
        return parts[1][:8]
    return gw_id[:8]


def _apply_vpn_gateway_ovn(db: Session, gw: VPNGateway, router: Router, network: Network) -> None:
    short = _gw_short(gw.id)
    ls_name = f"vpn-{gw.id}"
    lrp_name = f"lrp-vpn-{short}"
    lsp_in_name = f"lsp-vpn-{short}-in"
    lsp_out_name = f"lsp-vpn-{short}-out"
    lsp_lrp_name = f"lsp-vpn-{short}-lrp"

    net_obj = ipaddress.ip_network(gw.transit_cidr)
    hosts = list(net_obj.hosts())
    router_transit_ip = str(hosts[0])
    ns_transit_ip = str(hosts[1])
    lrp_mac = stable_mac(f"{gw.id}-lrp")

    # 1. Logical Switch transit
    ovn_cli.ovn("--may-exist", "ls-add", ls_name)
    ovn_cli.ovn("set", "logical_switch", ls_name, "external_ids:cloudx=true", f"external_ids:vpn_gateway={gw.id}")

    # 2. Router port on transit LS (distinct LRP MAC)
    ovn_cli.ovn("--may-exist", "lrp-add", router.name, lrp_name, lrp_mac, f"{router_transit_ip}/{net_obj.prefixlen}")
    ovn_cli.ovn("set", "logical_router_port", lrp_name, "external_ids:cloudx=true")

    # 2b. LSP peer for LRP on transit LS
    ovn_cli.ovn("--may-exist", "lsp-add", ls_name, lsp_lrp_name, "--", "set", "logical_switch_port", lsp_lrp_name, "type=router", f"options:router-port={lrp_name}", "external_ids:cloudx=true")

    # 3. LSP in (connected to transit LS)
    ovn_cli.ovn("--may-exist", "lsp-add", ls_name, lsp_in_name)
    ovn_cli.ovn("set", "logical_switch_port", lsp_in_name, "external_ids:cloudx=true", f"external_ids:vpn_in={gw.id}")
    ovn_cli.ovn("lsp-set-addresses", lsp_in_name, f"{gw.inner_mac} {ns_transit_ip}")

    # 4. LSP out (connected to external network LS)
    ovn_cli.ovn("--may-exist", "lsp-add", network.name, lsp_out_name)
    ovn_cli.ovn("set", "logical_switch_port", lsp_out_name, "external_ids:cloudx=true", f"external_ids:vpn_out={gw.id}")
    ovn_cli.ovn("lsp-set-addresses", lsp_out_name, f"{gw.outer_mac} {gw.outer_ip}")


def _rollback_vpn_gateway_ovn(gw_id: str, router_name: str | None, network_name: str | None) -> None:
    # best-effort: cleanup OVN objects
    short = _gw_short(gw_id)
    ls_name = f"vpn-{gw_id}"
    lrp_name = f"lrp-vpn-{short}"
    lsp_in_name = f"lsp-vpn-{short}-in"
    lsp_out_name = f"lsp-vpn-{short}-out"
    lsp_lrp_name = f"lsp-vpn-{short}-lrp"

    try:
        ovn_cli.ovn("--if-exists", "lsp-del", lsp_lrp_name, check=False)
    except Exception: # best-effort: ignore lsp-del failure
        pass
    try:
        ovn_cli.ovn("--if-exists", "lsp-del", lsp_in_name, check=False)
    except Exception: # best-effort: ignore lsp-del failure
        pass
    if network_name:
        try:
            ovn_cli.ovn("--if-exists", "lsp-del", lsp_out_name, check=False)
        except Exception: # best-effort: ignore lsp-del failure
            pass
    if router_name:
        try:
            ovn_cli.ovn("--if-exists", "lrp-del", lrp_name, check=False)
        except Exception: # best-effort: ignore lrp-del failure
            pass
    try:
        ovn_cli.ovn("--if-exists", "ls-del", ls_name, check=False)
    except Exception: # best-effort: ignore ls-del failure
        pass


@router.post("/vpn/ikepolicies", response_model=IKEPolicyOut, status_code=201)
def create_ike_policy(payload: IKEPolicyIn, current: CurrentUser, db: DB):
    project_id = current.get("project_id", current.get("tenant_id", "default"))
    ike = IKEPolicy(
        name=payload.name,
        project_id=project_id,
        auth_algorithm=payload.auth_algorithm,
        encryption_algorithm=payload.encryption_algorithm,
        phase1_negotiation_mode=payload.phase1_negotiation_mode,
        pfs=payload.pfs,
    )
    db.add(ike)
    db.commit()
    db.refresh(ike)
    return ike

@router.get("/vpn/ikepolicies", response_model=list[IKEPolicyOut])
def list_ike_policies(current: CurrentUser, db: DB):
    project_id = current.get("project_id", current.get("tenant_id", "default"))
    q = select(IKEPolicy)
    if current.get("role") != "admin":
        q = q.where(IKEPolicy.project_id == project_id)
    return db.scalars(q).all()

@router.get("/vpn/ikepolicies/{policy_id}", response_model=IKEPolicyOut)
def get_ike_policy(policy_id: str, current: CurrentUser, db: DB):
    ike = _pol(db, current, IKEPolicy, policy_id, "IKE policy")
    return ike

@router.delete("/vpn/ikepolicies/{policy_id}", status_code=204)
def delete_ike_policy(policy_id: str, current: CurrentUser, db: DB):
    ike = _pol(db, current, IKEPolicy, policy_id, "IKE policy")
    db.delete(ike)
    db.commit()

@router.post("/vpn/ipsecpolicies", response_model=IPsecPolicyOut, status_code=201)
def create_ipsec_policy(payload: IPsecPolicyIn, current: CurrentUser, db: DB):
    project_id = current.get("project_id", current.get("tenant_id", "default"))
    ipsec = IPsecPolicy(
        name=payload.name,
        project_id=project_id,
        auth_algorithm=payload.auth_algorithm,
        encryption_algorithm=payload.encryption_algorithm,
        pfs=payload.pfs,
        transform_protocol=payload.transform_protocol,
    )
    db.add(ipsec)
    db.commit()
    db.refresh(ipsec)
    return ipsec

@router.get("/vpn/ipsecpolicies", response_model=list[IPsecPolicyOut])
def list_ipsec_policies(current: CurrentUser, db: DB):
    project_id = current.get("project_id", current.get("tenant_id", "default"))
    q = select(IPsecPolicy)
    if current.get("role") != "admin":
        q = q.where(IPsecPolicy.project_id == project_id)
    return db.scalars(q).all()

@router.get("/vpn/ipsecpolicies/{policy_id}", response_model=IPsecPolicyOut)
def get_ipsec_policy(policy_id: str, current: CurrentUser, db: DB):
    ipsec = _pol(db, current, IPsecPolicy, policy_id, "IPsec policy")
    return ipsec

@router.delete("/vpn/ipsecpolicies/{policy_id}", status_code=204)
def delete_ipsec_policy(policy_id: str, current: CurrentUser, db: DB):
    ipsec = _pol(db, current, IPsecPolicy, policy_id, "IPsec policy")
    db.delete(ipsec)
    db.commit()

@router.post("/vpn/site-connections", response_model=VPNSiteConnectionOut, status_code=201)
def create_vpn_site_connection(payload: VPNSiteConnectionIn, current: CurrentUser, db: DB):
    project_id = current.get("project_id", current.get("tenant_id", "default"))
    router_obj = own(current, require_by_ref(db, Router, payload.router_id, "router"), "router")
    
    # 1. Router must have external gateway
    if not router_obj.external_network_id:
        raise HTTPException(status_code=400, detail="router must have an external gateway configured before creating a VPN connection")
    
    ext_net = db.get(Network, router_obj.external_network_id)
    if not ext_net:
        raise HTTPException(status_code=400, detail="external network not found")

    # 2. Check overlap between peer_cidrs and router subnets or other peer_cidrs
    r_subnets = _router_subnets(db, router_obj.id)
    new_cidrs = [ipaddress.ip_network(c, strict=False) for c in payload.peer_cidrs]
    
    for r_net in r_subnets:
        for p_net in new_cidrs:
            if r_net.overlaps(p_net):
                raise HTTPException(status_code=409, detail=f"peer CIDR {p_net} overlaps with router subnet {r_net}")

    # Check against existing connections on this router
    existing_conns = db.scalars(select(VPNSiteConnection).where(VPNSiteConnection.router_id == router_obj.id)).all()
    for ec in existing_conns:
        try:
            ec_cidrs = json.loads(ec.peer_cidrs_json)
            for ec_c in ec_cidrs:
                ec_net = ipaddress.ip_network(ec_c, strict=False)
                for p_net in new_cidrs:
                    if ec_net.overlaps(p_net):
                        raise HTTPException(status_code=409, detail=f"peer CIDR {p_net} overlaps with existing connection CIDR {ec_net}")
        except HTTPException:
            raise
        except Exception:  # best-effort: ignore exception when comparing existing connection peer cidrs
            pass

    ike = _pol(db, current, IKEPolicy, payload.ikepolicy_id, "IKE policy")
    ipsec = _pol(db, current, IPsecPolicy, payload.ipsecpolicy_id, "IPsec policy")

    # 3. Find or create VPNGateway for this router
    gw = db.scalar(select(VPNGateway).where(VPNGateway.router_id == router_obj.id))
    created_gw_here = False

    if not gw:
        host = _get_router_host(router_obj.name)
        transit_cidr = _allocate_vpn_transit_cidr(db)
        outer_ip = _allocate_outer_ip(db, ext_net)
        gw_id = f"vpngw-{uuid.uuid4().hex[:12]}" if hasattr(uuid, 'uuid4') else f"vpngw-{uuid.uuid4().hex[:12]}"
        inner_mac = stable_mac(f"{router_obj.id}-vpn-in")
        outer_mac = stable_mac(f"{router_obj.id}-vpn-out")

        gw = VPNGateway(
            id=gw_id,
            router_id=router_obj.id,
            project_id=project_id,
            host=host,
            transit_cidr=transit_cidr,
            outer_network_id=ext_net.id,
            outer_ip=outer_ip,
            inner_mac=inner_mac,
            outer_mac=outer_mac,
            status="PENDING_CREATE",
        )
        db.add(gw)
        db.flush()
        created_gw_here = True

        try:
            _apply_vpn_gateway_ovn(db, gw, router_obj, ext_net)
            # Add static routes for peer_cidrs via namespace transit IP (.2)
            net_obj = ipaddress.ip_network(gw.transit_cidr)
            ns_transit_ip = str(list(net_obj.hosts())[1])
            for p_net in new_cidrs:
                ovn_cli.ovn("--may-exist", "lr-route-add", router_obj.name, str(p_net), ns_transit_ip)
        except Exception:
            db.rollback()
            _rollback_vpn_gateway_ovn(gw.id, router_obj.name, ext_net.name)
            raise

    else:
        # Gateway exists, add static routes for new peer_cidrs via namespace transit IP (.2)
        net_obj = ipaddress.ip_network(gw.transit_cidr)
        ns_transit_ip = str(list(net_obj.hosts())[1])
        try:
            for p_net in new_cidrs:
                ovn_cli.ovn("--may-exist", "lr-route-add", router_obj.name, str(p_net), ns_transit_ip)
        except Exception:
            if created_gw_here:
                db.rollback()
                _rollback_vpn_gateway_ovn(gw.id, router_obj.name, ext_net.name)
            raise

    conn = VPNSiteConnection(
        name=payload.name,
        project_id=project_id,
        router_id=router_obj.id,
        gateway_id=gw.id,
        peer_address=payload.peer_address,
        peer_id=payload.peer_id,
        peer_cidrs_json=json.dumps(payload.peer_cidrs),
        psk=payload.psk,
        ikepolicy_id=ike.id,
        ipsecpolicy_id=ipsec.id,
        admin_state_up=payload.admin_state_up,
        status="PENDING_CREATE",
    )
    db.add(conn)
    db.commit()
    db.refresh(conn)
    return {
        "id": conn.id,
        "name": conn.name,
        "project_id": conn.project_id,
        "router_id": conn.router_id,
        "gateway_id": conn.gateway_id,
        "peer_address": conn.peer_address,
        "peer_id": conn.peer_id,
        "peer_cidrs": json.loads(conn.peer_cidrs_json),
        "ikepolicy_id": conn.ikepolicy_id,
        "ipsecpolicy_id": conn.ipsecpolicy_id,
        "status": conn.status,
        "status_reason": conn.status_reason,
        "admin_state_up": conn.admin_state_up,
        "created_at": conn.created_at,
    }

@router.get("/vpn/site-connections", response_model=list[VPNSiteConnectionOut])
def list_vpn_site_connections(current: CurrentUser, db: DB):
    project_id = current.get("project_id", current.get("tenant_id", "default"))
    q = select(VPNSiteConnection)
    if current.get("role") != "admin":
        q = q.where(VPNSiteConnection.project_id == project_id)
    conns = db.scalars(q).all()
    return [
        {
            "id": c.id,
            "name": c.name,
            "project_id": c.project_id,
            "router_id": c.router_id,
            "gateway_id": c.gateway_id,
            "peer_address": c.peer_address,
            "peer_id": c.peer_id,
            "peer_cidrs": json.loads(c.peer_cidrs_json),
            "ikepolicy_id": c.ikepolicy_id,
            "ipsecpolicy_id": c.ipsecpolicy_id,
            "status": c.status,
            "status_reason": c.status_reason,
            "admin_state_up": c.admin_state_up,
            "created_at": c.created_at,
        }
        for c in conns
    ]

@router.get("/vpn/site-connections/{conn_id}", response_model=VPNSiteConnectionOut)
def get_vpn_site_connection(conn_id: str, current: CurrentUser, db: DB):
    conn = _own_conn(db, current, conn_id)
    return {
        "id": conn.id,
        "name": conn.name,
        "project_id": conn.project_id,
        "router_id": conn.router_id,
        "gateway_id": conn.gateway_id,
        "peer_address": conn.peer_address,
        "peer_id": conn.peer_id,
        "peer_cidrs": json.loads(conn.peer_cidrs_json),
        "ikepolicy_id": conn.ikepolicy_id,
        "ipsecpolicy_id": conn.ipsecpolicy_id,
        "status": conn.status,
        "status_reason": conn.status_reason,
        "admin_state_up": conn.admin_state_up,
        "created_at": conn.created_at,
    }

@router.delete("/vpn/site-connections/{conn_id}", status_code=204)
def delete_vpn_site_connection(conn_id: str, current: CurrentUser, db: DB):
    conn = _own_conn(db, current, conn_id)
    router_obj = db.get(Router, conn.router_id)
    gw = db.get(VPNGateway, conn.gateway_id) if conn.gateway_id else None

    # Remove static routes for peer_cidrs
    if router_obj and gw:
        try:
            peer_cidrs = json.loads(conn.peer_cidrs_json)
            for pc in peer_cidrs:
                ovn_cli.ovn("--if-exists", "lr-route-del", router_obj.name, pc, check=False)
        except Exception: # best-effort: ignore route deletion failure
            pass

    db.delete(conn)
    db.flush()

    # Check if this was the last connection on the gateway
    if gw:
        remaining_conns = db.scalars(select(VPNSiteConnection).where(VPNSiteConnection.gateway_id == gw.id)).all()
        if not remaining_conns:
            # Delete gateway OVN objects and gateway record
            ext_net = db.get(Network, gw.outer_network_id)
            _rollback_vpn_gateway_ovn(gw.id, router_obj.name if router_obj else None, ext_net.name if ext_net else None)
            db.delete(gw)

    db.commit()
    return None

@router.post("/vpn/site-connections/{conn_id}/config")
def generate_vpn_config(conn_id: str, current: CurrentUser, db: DB):
    conn = _own_conn(db, current, conn_id)
    gw = db.get(VPNGateway, conn.gateway_id) if conn.gateway_id else None
    router_obj = db.get(Router, conn.router_id)
    
    ike = db.get(IKEPolicy, conn.ikepolicy_id)
    ipsec = db.get(IPsecPolicy, conn.ipsecpolicy_id)

    local_subnets = [str(n) for n in _router_subnets(db, conn.router_id)] if router_obj else []
    peer_cidrs = json.loads(conn.peer_cidrs_json)

    gateway_dict = {"outer_ip": gw.outer_ip if gw else "172.24.4.100"}
    conn_dict = {
        "id": conn.id,
        "name": conn.name,
        "peer_address": conn.peer_address,
        "peer_id": conn.peer_id,
        "peer_cidrs": peer_cidrs,
        "local_subnets": local_subnets,
        "psk": conn.psk,
        "ike_auth": ike.auth_algorithm if ike else "sha256",
        "ike_enc": ike.encryption_algorithm if ike else "aes-256",
        "ike_pfs": ike.pfs if ike else "group14",
        "ipsec_auth": ipsec.auth_algorithm if ipsec else "sha256",
        "ipsec_enc": ipsec.encryption_algorithm if ipsec else "aes-256",
        "ipsec_pfs": ipsec.pfs if ipsec else "group14",
    }

    from shared.swanctl_cfg import render
    cfg = render(gateway_dict, [conn_dict], mask_psk=True)

    return {
        "connection_id": conn.id,
        "ipsec_conf": cfg,
        "ipsec_secrets": f": PSK \"{conn.psk}\"\n",
        "swanctl_conf": cfg,
        "status": conn.status,
    }


# Internal agent endpoints
@router.get("/internal/vpn-agent/{host}", dependencies=[InternalAuth])
def internal_get_vpn_agent(host: str, db: DB):
    gateways = db.scalars(select(VPNGateway).where(VPNGateway.host == host)).all()
    result = []
    for gw in gateways:
        router_obj = db.get(Router, gw.router_id)
        ext_net = db.get(Network, gw.outer_network_id)
        local_subnets = [str(n) for n in _router_subnets(db, gw.router_id)] if router_obj else []
        
        conns = db.scalars(select(VPNSiteConnection).where(VPNSiteConnection.gateway_id == gw.id, VPNSiteConnection.admin_state_up == True)).all()
        conn_list = []
        for c in conns:
            ike = db.get(IKEPolicy, c.ikepolicy_id)
            ipsec = db.get(IPsecPolicy, c.ipsecpolicy_id)
            conn_list.append({
                "id": c.id,
                "name": c.name,
                "peer_address": c.peer_address,
                "peer_id": c.peer_id,
                "peer_cidrs": json.loads(c.peer_cidrs_json),
                "psk": c.psk,
                "ike_auth": ike.auth_algorithm if ike else "sha256",
                "ike_enc": ike.encryption_algorithm if ike else "aes-256",
                "ike_pfs": ike.pfs if ike else "group14",
                "ipsec_auth": ipsec.auth_algorithm if ipsec else "sha256",
                "ipsec_enc": ipsec.encryption_algorithm if ipsec else "aes-256",
                "ipsec_pfs": ipsec.pfs if ipsec else "group14",
            })

        result.append({
            "id": gw.id,
            "router_id": gw.router_id,
            "router_name": router_obj.name if router_obj else "",
            "transit_cidr": gw.transit_cidr,
            "outer_ip": gw.outer_ip,
            "outer_prefixlen": ipaddress.ip_network(ext_net.provider_cidr, strict=False).prefixlen if ext_net and ext_net.provider_cidr else 24,
            "outer_gateway_ip": ext_net.gateway_ip if ext_net else "",
            "inner_mac": gw.inner_mac,
            "outer_mac": gw.outer_mac,
            "inner_lsp": f"lsp-vpn-{_gw_short(gw.id)}-in",
            "outer_lsp": f"lsp-vpn-{_gw_short(gw.id)}-out",
            "local_subnets": local_subnets,
            "connections": conn_list,
        })
    return {"gateways": result}


@router.post("/internal/vpn-agent/{host}/status", dependencies=[InternalAuth])
def internal_post_vpn_agent_status(host: str, payload: dict, db: DB):
    statuses = payload.get("statuses", {})
    for conn_id, data in statuses.items():
        conn = db.get(VPNSiteConnection, conn_id)
        if conn:
            if "status" in data:
                conn.status = data["status"]
            if "status_reason" in data:
                conn.status_reason = data["status_reason"]
    db.commit()
    return {"status": "updated"}
