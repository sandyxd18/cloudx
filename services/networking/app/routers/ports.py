from fastapi import APIRouter, HTTPException
from shared.auth_middleware import CurrentUser
from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from .. import ovn_cli
from ..config import log
from ..db import DB
from ..events import _publish_net_event
from ..ipam import allocate_fixed_ip, format_id_list, parse_id_list
from ..models import FloatingIP, Network, Port, Router, SecurityGroup, Subnet, mac
from ..ovn_cli import _ovn_register_dns, apply_security_groups, port_group_name
from ..schemas import PortIn, PortSecurityGroupsIn, PortUpdate
from ..serializers import get_by_ref, port_to_dict, require_by_ref

router = APIRouter()


@router.post("/ports", status_code=201)
def create_port(payload: PortIn, current: CurrentUser, db: DB):
    if get_by_ref(db, Port, payload.name):
        raise HTTPException(status_code=409, detail="port already exists")
    network = require_by_ref(db, Network, payload.network, "network")
    subnet = db.scalar(select(Subnet).where(Subnet.network_id == network.id).where(Subnet.ip_version == 4).order_by(Subnet.created_at))
    requested_ip = payload.fixed_ip or payload.ip_address

    # Honor fixed_ips list (dual-stack) if provided
    if payload.fixed_ips:
        for fip_entry in payload.fixed_ips:
            entry_subnet_id = fip_entry.get("subnet_id")
            entry_ip = fip_entry.get("ip_address")
            if entry_subnet_id:
                entry_subnet = db.get(Subnet, entry_subnet_id)
                if entry_subnet and entry_subnet.ip_version == 4:
                    requested_ip = requested_ip or entry_ip
                    subnet = entry_subnet

    fixed_ip = allocate_fixed_ip(db, subnet, requested_ip) if subnet else requested_ip
    port_mac = mac()
    logical_switch_name = network.name
    logical_port_name = payload.name
    security_group_ids = payload.security_group_ids or ["default"]
    groups = [require_by_ref(db, SecurityGroup, group_id, "security-group") for group_id in security_group_ids]

    # Dual-stack: look for an IPv6 subnet on the same network if fixed_ips requests it
    ipv6_address: str | None = None
    if payload.fixed_ips:
        for fip_entry in payload.fixed_ips:
            entry_subnet_id = fip_entry.get("subnet_id")
            entry_ip = fip_entry.get("ip_address")
            if entry_subnet_id:
                entry_subnet = db.get(Subnet, entry_subnet_id)
                if entry_subnet and entry_subnet.ip_version == 6:
                    ipv6_address = allocate_fixed_ip(db, entry_subnet, entry_ip) if not entry_ip else entry_ip

    port = Port(
        name=payload.name,
        network_id=network.id,
        subnet_id=subnet.id if subnet else None,
        mac_address=port_mac,
        ip_address=fixed_ip,
        fixed_ip=fixed_ip,
        ipv6_address=ipv6_address,
        dns_name=payload.dns_name,
        logical_switch_name=logical_switch_name,
        logical_port_name=logical_port_name,
        device_id=payload.device_id,
        device_owner=payload.device_owner,
        security_group_ids=format_id_list([group.id for group in groups]),
        port_security_enabled=payload.port_security_enabled,
        qos_max_kbps=payload.qos_max_kbps,
        qos_burst_kbps=payload.qos_burst_kbps,
    )
    ovn_cli.ovn("--may-exist", "lsp-add", network.name, port.name)
    ovn_cli.ovn("set", "logical_switch_port", port.name, "external_ids:cloudx=true")
    if payload.device_id:
        ovn_cli.ovn("set", "logical_switch_port", port.name, f"external_ids:device_id={payload.device_id}")
    if payload.device_owner:
        ovn_cli.ovn("set", "logical_switch_port", port.name, f"external_ids:device_owner={payload.device_owner}")

    # Build OVN address string — include both IPv4 and IPv6 if dual-stack
    addr_parts = [port_mac]
    if fixed_ip:
        addr_parts.append(fixed_ip)
    if ipv6_address:
        addr_parts.append(ipv6_address)
    ovn_cli.ovn("lsp-set-addresses", port.name, " ".join(addr_parts))

    if subnet and subnet.dhcp_options_id:
        ovn_cli.ovn("lsp-set-dhcpv4-options", port.name, subnet.ovn_dhcp_options_uuid or subnet.dhcp_options_id)

    # Register DNS entry in OVN NB DB if dns_name provided
    # This enables VM-to-VM hostname resolution within the network.
    # Future: a Designate-like service can read these records via the API.
    if payload.dns_name and fixed_ip:
        _ovn_register_dns(network.name, payload.dns_name, fixed_ip)

    db.add(port)
    db.commit()
    db.refresh(port)
    apply_security_groups(db, port)
    _publish_net_event("port.create.end", {
        "port_id":    port.id,
        "name":       port.name,
        "network_id": port.network_id,
        "fixed_ip":   port.fixed_ip,
        "device_id":  port.device_id,
    })
    return port_to_dict(db, port)

@router.get("/ports")
def list_ports(_: CurrentUser, db: DB):
    return {"items": [port_to_dict(db, p) for p in db.scalars(select(Port).order_by(Port.created_at)).all()]}

@router.get("/ports/{ref}")
def show_port(ref: str, _: CurrentUser, db: DB):
    return port_to_dict(db, require_by_ref(db, Port, ref, "port"))

@router.post("/ports/{ref}/security-groups")
def set_port_security_groups(ref: str, payload: PortSecurityGroupsIn, _: CurrentUser, db: DB):
    port = require_by_ref(db, Port, ref, "port")
    groups = [require_by_ref(db, SecurityGroup, item, "security-group") for item in payload.security_group_ids]
    port.security_group_ids = format_id_list([group.id for group in groups])
    db.commit()
    db.refresh(port)
    apply_security_groups(db, port)
    return port_to_dict(db, port)

@router.post("/ports/{ref}/security-groups/clear")
def clear_port_security_groups(ref: str, _: CurrentUser, db: DB):
    port = require_by_ref(db, Port, ref, "port")
    port.security_group_ids = None
    db.commit()
    db.refresh(port)
    apply_security_groups(db, port)
    return port_to_dict(db, port)

@router.post("/ports/{ref}/security/enable")
def enable_port_security(ref: str, db: DB):
    port = require_by_ref(db, Port, ref, "port")
    port.port_security_enabled = True
    if not parse_id_list(port.security_group_ids):
        default_group = get_by_ref(db, SecurityGroup, "default")
        if default_group:
            port.security_group_ids = format_id_list([default_group.id])
    db.commit()
    db.refresh(port)
    apply_security_groups(db, port)
    return port_to_dict(db, port)

@router.post("/ports/{ref}/security/disable")
def disable_port_security(ref: str, db: DB):
    port = require_by_ref(db, Port, ref, "port")
    port.port_security_enabled = False
    db.commit()
    db.refresh(port)
    apply_security_groups(db, port)
    return port_to_dict(db, port)

@router.patch("/ports/{ref}")
@router.put("/ports/{ref}")
def update_port(ref: str, payload: PortUpdate, _: CurrentUser, db: DB):
    port = require_by_ref(db, Port, ref, "port")
    if payload.name is not None:
        port.name = payload.name
    if payload.qos_max_kbps is not None:
        port.qos_max_kbps = payload.qos_max_kbps
    if payload.qos_burst_kbps is not None:
        port.qos_burst_kbps = payload.qos_burst_kbps
    db.commit()
    db.refresh(port)
    return port_to_dict(db, port)

@router.get("/ports/{ref}/ovn")
def port_ovn_show(ref: str, db: DB):
    port = require_by_ref(db, Port, ref, "port")
    groups = []
    for group_id in parse_id_list(port.security_group_ids):
        group = db.get(SecurityGroup, group_id)
        if group:
            groups.append({"id": group.id, "name": group.name, "port_group": port_group_name(group.id)})
    return {
        "port": port_to_dict(db, port),
        "logical_port_name": port.logical_port_name,
        "port_security_enabled": port.port_security_enabled,
        "security_groups": groups,
    }

@router.delete("/ports/{ref}")
def delete_port(ref: str, _: CurrentUser, db: DB):
    port = require_by_ref(db, Port, ref, "port")
    port.security_group_ids = None
    apply_security_groups(db, port)
    ovn_cli.ovn("--if-exists", "lsp-del", port.name)
    db.delete(port)
    db.commit()
    return {"status": "deleted", "id": port.id, "name": port.name}

# ── Internal port management (service-to-service, no auth) ────────────────────
# Mirrors block-store's /internal/volumes pattern: compute-worker calls these
# during instance deletion, where it has no user auth token to present.

def _disassociate_floating_ips_for_port(db: Session, port: Port) -> list[str]:
    """Reset any floating IPs bound to this port: remove the OVN NAT rule and
    clear the association so the FIP returns to DOWN/unallocated-binding state.

    Called before a port is deleted (instance teardown / purge) — otherwise the
    FIP row keeps pointing at a port that no longer exists.
    Returns the list of released floating IP addresses."""
    fips = db.scalars(
        select(FloatingIP).where(
            or_(FloatingIP.port_id == port.id,
                FloatingIP.fixed_port_id == port.id)
        )
    ).all()
    released = []
    for fip in fips:
        router = db.get(Router, fip.router_id) if fip.router_id else None
        if router and fip.floating_ip:
            ovn_cli.ovn("--if-exists", "lr-nat-del", router.name, "dnat_and_snat", fip.floating_ip, check=False)
        fip.fixed_port_id = None
        fip.port_id = None
        fip.fixed_ip_address = None
        fip.server_id = None
        fip.router_id = None
        fip.status = "DOWN"
        released.append(fip.floating_ip or fip.floating_ip_address or fip.id)
    if fips:
        db.commit()
    return released

def _delete_port_record(db: Session, port: Port) -> dict:
    _disassociate_floating_ips_for_port(db, port)
    port.security_group_ids = None
    apply_security_groups(db, port)
    ovn_cli.ovn("--if-exists", "lsp-del", port.name)
    db.delete(port)
    db.commit()
    return {"status": "deleted", "id": port.id, "name": port.name}

@router.delete("/internal/ports/{ref}", status_code=200)
def delete_port_internal(ref: str, db: DB):
    """Service-to-service port deletion — used by compute-worker on instance teardown."""
    port = require_by_ref(db, Port, ref, "port")
    return _delete_port_record(db, port)

@router.post("/internal/ports/purge-orphaned")
def purge_orphaned_ports(db: DB):
    """Delete compute-owned ports whose server no longer exists, and release
    floating IPs pointing at ports that are already gone.

    Historical safety net: before /internal/ports existed, compute-worker's
    port cleanup failed silently (401 against the authed endpoint), so ports
    of deleted instances accumulated; floating IPs bound to those ports kept
    referencing dead fixed IPs. A port is orphaned when its device_owner
    starts with 'compute:' and its device_id no longer matches any live
    compute_servers row (all services share the same PostgreSQL DB).
    """
    compute_ports = db.scalars(
        select(Port).where(Port.device_owner.like("compute:%"), Port.device_id.is_not(None))
    ).all()
    deleted = []
    for port in compute_ports:
        live = db.execute(
            text("SELECT 1 FROM compute_servers WHERE id = :sid AND status != 'DELETED'"),
            {"sid": port.device_id},
        ).first()
        if not live:
            try:
                _delete_port_record(db, port)
                deleted.append({"id": port.id, "name": port.name, "device_id": port.device_id})
            except Exception as exc:
                log.warning("could not purge orphaned port %s: %s", port.id, exc)
                continue

    # Floating IPs whose port_id no longer resolves to any port row —
    # leftovers from deletions that happened before the port-delete hook
    # existed. Release them the same way disassociate does.
    stale_fips = db.scalars(
        select(FloatingIP).where(FloatingIP.port_id.is_not(None))
    ).all()
    live_port_ids = {p.id for p in db.scalars(select(Port)).all()}
    released_fips = []
    for fip in stale_fips:
        if fip.port_id in live_port_ids:
            continue
        router = db.get(Router, fip.router_id) if fip.router_id else None
        if router and fip.floating_ip:
            ovn_cli.ovn("--if-exists", "lr-nat-del", router.name, "dnat_and_snat", fip.floating_ip, check=False)
        fip.fixed_port_id = None
        fip.port_id = None
        fip.fixed_ip_address = None
        fip.server_id = None
        fip.router_id = None
        fip.status = "DOWN"
        released_fips.append(fip.floating_ip or fip.floating_ip_address or fip.id)
    if released_fips:
        db.commit()

    return {"purged": len(deleted), "ports": deleted, "released_floating_ips": released_fips}
