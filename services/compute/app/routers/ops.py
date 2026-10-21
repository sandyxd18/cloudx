"""Nova operator surface: instance actions, os-services, limits, cold migrate, evacuate."""
from datetime import datetime

import requests
from fastapi import APIRouter, HTTPException, Request
from shared.auth_middleware import AdminOrInternal, AdminUser, CurrentUser, InternalAuth, UserOrInternal
from shared.internal import internal_headers
from sqlalchemy import select

from .. import clients
from ..clients import response_detail
from ..config import AUTH_API_URL, block_store_api_url, networking_api_url
from ..db import DB
from ..lookups import get_flavor, get_server, require_server
from ..models import ComputeService, Flavor, Server, ServerAction
from ..schemas import MoveIn, ServiceUpdateIn
from ..service import claim_host, compute_hosts, fallback_host, finish_action, project_limits, record_action, server_worker_payload

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


@router.post("/internal/servers/{server_id}/actions/finish", dependencies=[InternalAuth])
def internal_finish_action(server_id: str, payload: dict, db: DB):
    finish_action(db, server_id, result=payload.get("result", "success"), message=payload.get("message"), action=payload.get("action"))
    return {"ok": True}


@router.get("/internal/servers", dependencies=[InternalAuth])
def internal_list_servers(db: DB):
    rows = db.scalars(select(Server).where(Server.status != "DELETED")).all()
    return {"servers": [{"id": s.id, "host": s.host, "libvirt_domain_name": s.libvirt_domain_name, "status": s.status} for s in rows]}


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
        r = requests.request(method, f"{AUTH_API_URL}/projects/{project}/quota", headers=headers, timeout=5, **kw)
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
def update_quota_set(project: str, payload: dict, request: Request, _: AdminUser):
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
def update_service(host: str, payload: ServiceUpdateIn, _: AdminOrInternal, db: DB):
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


def _relocate(ref: str, host: str | None, current: dict, db, evacuate: bool) -> dict:
    server = require_server(db, ref, current)
    ok = {"ACTIVE", "SHUTOFF", "ERROR"} if evacuate else {"ACTIVE", "SHUTOFF"}
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
    extra = [v for v in clients.block_get("/volumes").get("items", [])
             if v.get("attached_to_server_id") == server.id and v["id"] != server.root_volume_id]
    data_vols = [{"id": v["id"], "target_device": (v.get("target_device") or v.get("mountpoint") or "sdb").split("/")[-1]} for v in extra]
    flavor = _flavor_of(db, server)
    port = clients.api_get(f"{networking_api_url()}/ports/{server.port_id}")
    ports = [port] + [clients.api_get(f"{networking_api_url()}/ports/{p}") for p in (server.extra_port_ids or "").split(",") if p]
    root = clients.block_get(f"/volumes/{server.root_volume_id}")
    target = claim_host(db, server.id, server.project_id, flavor, host=host, avoid=(old,))
    if target is None:
        target = fallback_host(db, flavor, host=host, avoid=(old,))
    if target is None:
        raise HTTPException(status_code=409, detail="No valid host was found")
    old_payload = server_worker_payload(server)
    server.host, server.status = target, "REBUILD" if evacuate else "MIGRATING"
    db.commit()
    build = server_worker_payload(server, port, ports, root)
    build["_root_volume_id"], build["_block_store_url"] = server.root_volume_id, block_store_api_url()
    build["_target_status"] = target_status
    if data_vols:
        build["_data_volumes"] = data_vols
    record_action(db, server.id, "evacuate" if evacuate else "migrate", current)
    if evacuate:  # source is down: free the volume record here, nothing to tear down
        clients.release_server_volumes(server.id)
        clients.cast_to_worker("build_instance", build)
    else:  # source worker tears the domain down, then casts the build to the target (keeps the order)
        clients.cast_to_worker("relocate_out", {**old_payload, "_build": build, "_target_host": target,
                                                "_root_volume_id": server.root_volume_id, "_block_store_url": block_store_api_url()})
    return {"server_id": server.id, "from": old, "to": target, "status": server.status}


@router.post("/servers/{ref}/migrate", status_code=202)
def migrate(ref: str, current: AdminUser, db: DB, payload: MoveIn | None = None):
    return _relocate(ref, payload.host if payload else None, current, db, False)


@router.post("/servers/{ref}/evacuate", status_code=202)
def evacuate(ref: str, current: AdminOrInternal, db: DB, payload: MoveIn | None = None):
    return _relocate(ref, payload.host if payload else None, current, db, True)
