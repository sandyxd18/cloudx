"""Nova operator surface: instance actions, os-services, limits, cold migrate, live migrate, evacuate."""
from datetime import datetime, timedelta
from typing import Annotated

import requests
from fastapi import APIRouter, Depends, HTTPException, Request
from shared import policy
from shared.auth_middleware import CurrentUser, InternalAuth, UserOrInternal, require_user_or_internal
from shared.internal import internal_headers
from shared.refs import path_ref
from sqlalchemy import select, update

from .. import clients
from ..clients import response_detail
from ..config import AUTH_API_URL, block_store_api_url, log, networking_api_url, placement_api_url
from ..db import DB
from ..lookups import get_flavor, get_server, require_server, require_unlocked_server
from ..models import ComputeService, Flavor, Server, ServerAction, now
from ..schemas import MoveIn, ServiceUpdateIn
from ..service import (
    claim_host,
    compute_hosts,
    fallback_host,
    finish_action,
    project_limits,
    record_action,
    release_allocation,
    server_worker_payload,
)

router = APIRouter()


def log_action(request: Request, current: UserOrInternal, db: DB) -> None:
    """Router dependency for /servers/{ref}/<action>: logs the request (not its outcome)."""
    if request.method == "GET":
        return
    seg = request.url.path.strip("/").split("/")
    i = seg.index("servers") if "servers" in seg else -1
    if i < 0 or len(seg) < i + 2 or (len(seg) == i + 2 and request.method != "DELETE"):
        return
    server = get_server(db, seg[i + 1])
    if not server or (current.get("role") != "admin" and server.project_id != current.get("project_id")):
        return
    action = seg[i + 2] if len(seg) > i + 2 else "delete"
    record_action(db, server.id, f"delete_{action}" if request.method == "DELETE" and len(seg) > i + 2 else action, current)


@router.get("/servers/{ref}/os-instance-actions")
def instance_actions(ref: str, current: CurrentUser, db: DB):
    server = require_server(db, ref, current)
    rows = db.scalars(select(ServerAction).where(ServerAction.server_id == server.id).order_by(ServerAction.start_time.desc())).all()
    return {"instanceActions": [{"request_id": r.id, "instance_uuid": r.server_id, "action": r.action,
                                 "user_id": r.user_id, "project_id": r.project_id,
                                 "start_time": r.start_time.isoformat() if r.start_time else None,
                                 "result": r.result, "message": r.message,
                                 "finish_time": r.finish_time.isoformat() if r.finish_time else None} for r in rows]}


@router.get("/servers/{ref}/os-instance-actions/{request_id}")
def show_instance_action(ref: str, request_id: str, current: CurrentUser, db: DB):
    server = require_server(db, ref, current)
    r = db.scalar(select(ServerAction).where(ServerAction.server_id == server.id, ServerAction.id == request_id))
    if not r:
        raise HTTPException(status_code=404, detail="instance action not found")
    return {"instanceAction": {"request_id": r.id, "instance_uuid": r.server_id, "action": r.action,
                               "user_id": r.user_id, "project_id": r.project_id,
                               "start_time": r.start_time.isoformat() if r.start_time else None,
                               "result": r.result, "message": r.message,
                               "finish_time": r.finish_time.isoformat() if r.finish_time else None}}


def _settle_live_migration(db, server_id: str, result: str, target_host: str | None) -> None:
    """The worker sets host and status through a conductor cast, which can be lost while this HTTP notify arrives.
    So a server that is still MIGRATING is settled here as well: on success it is ACTIVE on the target (only when the
    worker named it: ACTIVE on the source would let the target's reconciler destroy the moved guest), on error ACTIVE
    on its host. A server in any other status (DELETING, DELETED, already settled) is left alone."""
    if result == "success" and target_host:
        values = {"host": target_host, "status": "ACTIVE", "error_message": None}
    elif result == "error":
        values = {"status": "ACTIVE"}
    else:
        return
    db.execute(update(Server).where(Server.id == server_id, Server.status == "MIGRATING").values(**values))
    db.commit()


@router.post("/internal/servers/{server_id}/actions/finish", dependencies=[InternalAuth])
def internal_finish_action(server_id: str, payload: dict, db: DB):
    result, action = payload.get("result", "success"), payload.get("action")
    finish_action(db, server_id, result=result, message=payload.get("message"), action=action)
    if action == "shelve" and result == "success":  # an offloaded server holds no vCPU or memory on any node
        release_allocation(server_id)
    if action == "live_migrate":
        _settle_live_migration(db, server_id, result, payload.get("target_host"))
    if action == "live_migrate" and result == "error":
        # the domain stayed on the source; the claim moved to the target when the migration began. On "success" with a
        # message (a cleanup problem after the domain left) the server lives on the target, so the claim stays there.
        server = db.get(Server, server_id)
        if server and server.host:
            try:
                restore_source_claim(db, server)
            except Exception as exc:  # the action is finished either way; an operator can re-claim later
                log.error("live migration of %s failed and its placement claim could not be put back on %s: %s",
                          server_id, server.host, exc)
    return {"ok": True}


@router.get("/internal/servers", dependencies=[InternalAuth])
def internal_list_servers(db: DB):
    rows = db.scalars(select(Server).where(Server.status != "DELETED")).all()
    return {"servers": [{"id": s.id, "host": s.host, "libvirt_domain_name": s.libvirt_domain_name, "status": s.status} for s in rows]}


@router.get("/internal/resize-pending", dependencies=[InternalAuth])
def internal_resize_pending(seconds: int, db: DB):
    """Servers that have sat in VERIFY_RESIZE for at least `seconds` (the reaper confirms them). The status filter is
    needed because some paths write status without clearing the timestamp; NULL never matches the comparison."""
    cutoff = now() - timedelta(seconds=seconds)
    rows = db.scalars(select(Server).where(Server.status == "VERIFY_RESIZE", Server.resize_pending_since <= cutoff)).all()
    return {"items": [{"id": s.id} for s in rows]}


@router.get("/limits")
def limits(current: CurrentUser, db: DB):
    return project_limits(db, current["project_id"])


# Nova quota-set name -> auth-api ProjectQuota field. Storage is the only Cinder one kept here (gigabytes);
# the network names follow Neutron. Quota lives in auth-api, so this is a thin view over it.
QUOTA_MAP = {"instances": "max_instances", "cores": "max_vcpus", "ram": "max_ram_mb", "gigabytes": "max_volume_gb",
             "network": "max_networks", "subnet": "max_subnets", "port": "max_ports", "router": "max_routers",
             "floatingip": "max_floating_ips", "security_group": "max_security_groups"}


def _quota_set(q: dict) -> dict:
    return {"id": q["project_id"], **{k: q.get(v) for k, v in QUOTA_MAP.items()}}


def _auth(method: str, project: str, headers: dict, **kw) -> dict:
    try:
        r = requests.request(method, f"{AUTH_API_URL}/projects/{path_ref(project)}/quota", headers=headers, timeout=5, **kw)
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=response_detail(r))
    return r.json()


@router.get("/os-quota-sets/{project}")
def get_quota_set(project: str, current: CurrentUser):
    if current.get("role") != "admin" and project != current.get("project_id"):
        raise HTTPException(status_code=404, detail="project not found")
    return {"quota_set": _quota_set(_auth("GET", project, internal_headers()))}


@router.put("/os-quota-sets/{project}")
def update_quota_set(project: str, payload: dict, request: Request, _: Annotated[dict, Depends(policy.require("compute:quota:set"))]):
    body = payload.get("quota_set", payload)
    unknown = set(body) - set(QUOTA_MAP) - {"force"}
    if unknown:
        raise HTTPException(status_code=400, detail=f"unknown quota keys: {sorted(unknown)}")
    changes = {QUOTA_MAP[k]: v for k, v in body.items() if k in QUOTA_MAP}
    return {"quota_set": _quota_set(_auth("PUT", project, {"Authorization": request.headers.get("authorization", "")}, json=changes))}


def _service(row: ComputeService | None, host: str) -> dict:
    return {"binary": "compute-worker", "host": host, "status": "disabled" if row and row.disabled else "enabled",
            "disabled_reason": row.disabled_reason if row else None, "forced_down": bool(row and row.forced_down)}


@router.get("/os-services")
def list_services(_: UserOrInternal, db: DB):
    rows = {r.host: r for r in db.scalars(select(ComputeService))}
    return {"services": [_service(rows.get(h), h) for h in sorted(set(rows) | set(compute_hosts()))]}


@router.put("/os-services/{host}")
def update_service(host: str, payload: ServiceUpdateIn, _: Annotated[dict, Depends(policy.require("compute:service:update", authenticate=require_user_or_internal))], db: DB):
    row = db.get(ComputeService, host) or ComputeService(host=host, disabled=False, forced_down=False)
    db.add(row)
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(row, k, v)
    if row.disabled is False:
        row.disabled_reason = None
    row.updated_at = datetime.utcnow()
    db.commit()
    return {"service": _service(row, host)}


def _flavor_of(db, server) -> Flavor:
    # the flavor row may be gone; the server keeps its own vcpus/ram snapshot
    return get_flavor(db, server.flavor) or Flavor(name=server.flavor_name or server.flavor, vcpus=server.vcpus or 1,
                                                    ram_mb=server.ram_mb or 512, disk_gb=server.disk_gb or 0)


def place_and_build(db, server, status: str, target_status: str, host: str | None = None, avoid: tuple = ()) -> tuple[str, dict, dict]:
    """Pick a node for a server whose domain is gone (or about to be), move the record there under `status` and return
    (node, the old node's payload, the build_instance payload). The build carries every port and every attached data
    volume, so the new domain has the NICs and disks the old one had."""
    extra = [v for v in clients.block_get("/volumes").get("items", [])
             if v.get("attached_to_server_id") == server.id and v["id"] != server.root_volume_id]
    data_vols = [{"id": v["id"], "target_device": (v.get("target_device") or v.get("mountpoint") or "sdb").split("/")[-1]} for v in extra]
    flavor = _flavor_of(db, server)
    port = clients.api_get(f"{networking_api_url()}/ports/{server.port_id}")
    ports = [port] + [clients.api_get(f"{networking_api_url()}/ports/{p}") for p in (server.extra_port_ids or "").split(",") if p]
    root = clients.block_get(f"/volumes/{server.root_volume_id}")
    block_store_url = block_store_api_url()
    target = claim_host(db, server.id, server.project_id, flavor, host=host, avoid=avoid)
    if target is None:
        target = fallback_host(db, flavor, host=host, avoid=avoid)
    if target is None:
        raise HTTPException(status_code=409, detail="No valid host was found")
    old_payload = server_worker_payload(server)
    server.host, server.status = target, status
    db.commit()
    build = server_worker_payload(server, port, ports, root)
    build["_root_volume_id"], build["_block_store_url"] = server.root_volume_id, block_store_url
    build["_target_status"] = target_status
    if data_vols:
        build["_data_volumes"] = data_vols
    return target, old_payload, build


def _relocate(ref: str, host: str | None, current: dict, db, evacuate: bool) -> dict:
    server = require_server(db, ref, current)
    # Evacuate also takes a MIGRATING server: it only runs once the source is forced down (checked below), and then
    # nothing else can take the server out of MIGRATING.
    ok = {"ACTIVE", "SHUTOFF", "ERROR", "MIGRATING"} if evacuate else {"ACTIVE", "SHUTOFF"}
    if server.status not in ok:
        raise HTTPException(status_code=409, detail=f"server status {server.status} cannot be {'evacuated' if evacuate else 'migrated'}")
    if not server.root_volume_id or not server.host:
        raise HTTPException(status_code=409, detail="server has no root volume or host")
    old = server.host
    orig_status = server.status
    target_status = orig_status if orig_status in ("ACTIVE", "SHUTOFF") else "ACTIVE"
    if evacuate:
        svc = db.get(ComputeService, old)
        if not (svc and svc.forced_down):
            raise HTTPException(status_code=409, detail=f"host {old} must be forced down first (PUT /os-services/{old} forced_down=true)")
    target, old_payload, build = place_and_build(db, server, "REBUILD" if evacuate else "MIGRATING", target_status, host=host, avoid=(old,))
    record_action(db, server.id, "evacuate" if evacuate else "migrate", current)
    if evacuate:  # source is down: free the volume record here, nothing to tear down
        clients.release_server_volumes(server.id)
        clients.cast_to_worker("build_instance", build)
    else:  # source worker tears the domain down, then casts the build to the target (keeps the order)
        clients.cast_to_worker("relocate_out", {**old_payload, "_build": build, "_target_host": target,
                                                "_root_volume_id": server.root_volume_id, "_block_store_url": build["_block_store_url"]})
    return {"server_id": server.id, "from": old, "to": target, "status": server.status}


@router.post("/servers/{ref}/migrate", status_code=202)
def migrate(ref: str, current: Annotated[dict, Depends(policy.require("compute:server:migrate"))], db: DB, payload: MoveIn | None = None):
    return _relocate(ref, payload.host if payload else None, current, db, False)


@router.post("/servers/{ref}/evacuate", status_code=202)
def evacuate(ref: str, current: Annotated[dict, Depends(policy.require("compute:server:evacuate", authenticate=require_user_or_internal))], db: DB, payload: MoveIn | None = None):
    return _relocate(ref, payload.host if payload else None, current, db, True)


def restore_source_claim(db, server) -> None:
    """Put the placement allocation of a server whose live migration failed back on its host (the source). The claim
    names the source as the only host and excludes none: claim_host would filter out a disabled source, and a drain
    disables the node before it live-migrates its servers. Raises when placement refuses or cannot be reached."""
    flavor = _flavor_of(db, server)
    r = requests.post(f"{placement_api_url()}/internal/claim", headers=internal_headers(), timeout=10, json={
        "consumer_uuid": server.id, "project_id": server.project_id,
        "resources": {"VCPU": flavor.vcpus, "MEMORY_MB": flavor.ram_mb},
        "exclude_hosts": [], "only_hosts": [server.host]})
    if r.status_code >= 400:
        raise RuntimeError(f"placement answered {r.status_code}: {response_detail(r)}")


def _volume_entry(volume: dict, target_dev: str) -> dict:
    return {"volume_id": volume["id"], "host": volume.get("host"), "path": volume.get("path"),
            "backend": volume.get("backend"), "target_dev": target_dev}


def live_migration_payload(db, server, host: str | None) -> tuple[str, dict]:
    """Check the server's volumes, claim a target (placement moves the allocation there) and return (target, payload
    of the `live_migrate` cast). The payload is the build payload (ports, root volume) plus `_volumes` (every disk,
    the root one as sda), `_source_host` and `_target_host`. The server record is not changed here."""
    root = clients.block_get(f"/volumes/{server.root_volume_id}")
    data = [v for v in clients.block_get("/volumes").get("items", [])
            if v.get("attached_to_server_id") == server.id and v["id"] != server.root_volume_id]
    for volume in [root, *data]:
        if volume.get("status") != "in-use":
            raise HTTPException(status_code=409, detail=f"volume {volume['id']} is {volume.get('status')}, not in-use")
        if not volume.get("path"):
            raise HTTPException(status_code=409, detail=f"volume {volume['id']} has no path")
    volumes = [_volume_entry(root, "sda")] + [
        _volume_entry(v, (v.get("target_device") or v.get("mountpoint") or "sdb").split("/")[-1]) for v in data]
    port = clients.api_get(f"{networking_api_url()}/ports/{server.port_id}")
    ports = [port] + [clients.api_get(f"{networking_api_url()}/ports/{p}") for p in (server.extra_port_ids or "").split(",") if p]
    flavor = _flavor_of(db, server)
    target = claim_host(db, server.id, server.project_id, flavor, host=host, avoid=(server.host,))
    if target is None:
        target = fallback_host(db, flavor, host=host, avoid=(server.host,))
    if target is None:
        raise HTTPException(status_code=409, detail="No valid host was found")
    payload = server_worker_payload(server, port, ports, root)
    payload.update({"_volumes": volumes, "_source_host": server.host, "_target_host": target})
    return target, payload


def _check_live_migration_target(db, server, host: str) -> None:
    if host == server.host:
        raise HTTPException(status_code=409, detail=f"server is already on {host}")
    svc = db.get(ComputeService, host)
    if svc and svc.disabled:
        raise HTTPException(status_code=409, detail=f"host {host} is disabled")
    if svc and svc.forced_down:
        raise HTTPException(status_code=409, detail=f"host {host} is forced down")
    known = compute_hosts()
    if known and host not in known:
        raise HTTPException(status_code=409, detail=f"host {host} is not a known compute node")


def _undo_live_migration_start(db, server, restore_claim: bool) -> None:
    """The request failed after the server went MIGRATING: back to ACTIVE, and the claim back on the source."""
    server.status = "ACTIVE"
    db.commit()
    if restore_claim:
        try:
            restore_source_claim(db, server)
        except Exception as exc:
            log.error("live migration of %s was not started and its placement claim could not be put back on %s: %s",
                      server.id, server.host, exc)


@router.post("/servers/{ref}/live-migrate", status_code=202)
def live_migrate(ref: str, current: Annotated[dict, Depends(policy.require("compute:server:live_migrate"))], db: DB, payload: MoveIn | None = None):
    """Move a running server to another node without stopping it. The source worker drives the migration and moves
    `host` once the domain runs on the target; until then the server stays on its old host, MIGRATING."""
    host = payload.host if payload else None
    server = require_unlocked_server(db, ref, current)
    if server.locked:  # require_unlocked_server lets an admin through; a locked server is never moved live
        raise HTTPException(status_code=409, detail="server is locked; unlock it first")
    if server.status != "ACTIVE":
        raise HTTPException(status_code=409, detail=f"server status {server.status} cannot be live-migrated (needs ACTIVE)")
    if not server.host or not server.root_volume_id:
        raise HTTPException(status_code=409, detail="server has no root volume or host")
    if not server.libvirt_domain_name:
        raise HTTPException(status_code=409, detail="server has no libvirt domain")
    if host:
        _check_live_migration_target(db, server, host)
    # ACTIVE -> MIGRATING in one statement: of two requests at once, only one gets past here and claims a target
    taken = db.execute(update(Server).where(Server.id == server.id, Server.status == "ACTIVE").values(status="MIGRATING"))
    db.commit()
    db.refresh(server)
    if taken.rowcount != 1:
        raise HTTPException(status_code=409, detail=f"server status {server.status} cannot be live-migrated (needs ACTIVE)")
    try:
        target, cast = live_migration_payload(db, server, host)
    except Exception:
        _undo_live_migration_start(db, server, restore_claim=False)  # nothing claimed: the claim is the last step
        raise
    record_action(db, server.id, "live_migrate", current)
    try:
        clients.cast_to_worker("live_migrate", cast)
    except Exception as exc:
        _undo_live_migration_start(db, server, restore_claim=True)
        finish_action(db, server.id, result="error", message=f"could not reach the compute worker: {exc}", action="live_migrate")
        raise HTTPException(status_code=503, detail=f"could not send live_migrate to the compute worker of {server.host}: {exc}") from exc
    return {"server_id": server.id, "from": server.host, "to": target, "status": server.status}
