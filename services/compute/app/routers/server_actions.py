import requests
from fastapi import APIRouter, HTTPException
from shared.auth_middleware import CurrentUser

from .. import clients
from ..clients import _get_svc_token
from ..config import (
    BLOCK_STORE_API_URL,
    IMAGE_API_URL,
    NETWORKING_API_URL,
    PLACEMENT_API_URL,
    log,
)
from ..db import DB
from ..lookups import require_flavor, require_server
from ..models import Flavor, ServerGroup
from ..schemas import (
    ServerPasswordIn,
    ServerPortIn,
    ServerRebootIn,
    ServerRebuildIn,
    ServerRescueIn,
    ServerResizeIn,
    ServerSnapshotIn,
    VolumeAttachIn,
)
from ..serializers import obj_to_dict
from ..service import comma_list, format_comma_list, server_worker_payload

router = APIRouter()


@router.post("/servers/{ref}/start")
def start_server(ref: str, db: DB):
    server = require_server(db, ref)
    server.status = "STARTING"
    db.commit()
    clients.cast_to_worker("start", server_worker_payload(server))
    db.refresh(server)
    return obj_to_dict(server)


@router.post("/servers/{ref}/stop")
def stop_server(ref: str, db: DB):
    server = require_server(db, ref)
    server.status = "STOPPING"
    db.commit()
    clients.cast_to_worker("stop", server_worker_payload(server))
    db.refresh(server)
    return obj_to_dict(server)


@router.post("/servers/{ref}/reboot")
def reboot_server(ref: str, payload: ServerRebootIn = None, _: CurrentUser = None, db: DB = None):
    server = require_server(db, ref)
    rtype = payload.type if payload else "soft"
    server.status = "REBOOT"
    db.commit()
    clients.cast_to_worker("reboot", {**server_worker_payload(server), "reboot_type": rtype})
    db.refresh(server)
    return obj_to_dict(server)


@router.post("/servers/{ref}/pause")
def pause_server(ref: str, _: CurrentUser, db: DB):
    server = require_server(db, ref)
    server.status = "PAUSING"
    db.commit()
    clients.cast_to_worker("pause", server_worker_payload(server))
    db.refresh(server)
    return obj_to_dict(server)


@router.post("/servers/{ref}/resume")
def resume_server(ref: str, _: CurrentUser, db: DB):
    server = require_server(db, ref)
    server.status = "RESUMING"
    db.commit()
    clients.cast_to_worker("resume", server_worker_payload(server))
    db.refresh(server)
    return obj_to_dict(server)


@router.post("/servers/{ref}/suspend")
def suspend_server(ref: str, _: CurrentUser, db: DB):
    server = require_server(db, ref)
    server.status = "SUSPENDING"
    db.commit()
    clients.cast_to_worker("suspend", server_worker_payload(server))
    db.refresh(server)
    return obj_to_dict(server)


@router.post("/servers/{ref}/resize")
def resize_server(ref: str, payload: ServerResizeIn, _: CurrentUser, db: DB):
    server = require_server(db, ref)
    flavor = require_flavor(db, payload.flavor)
    # Snapshot original flavor for potential revert
    server.original_flavor = server.flavor
    server.original_vcpus = server.vcpus
    server.original_ram_mb = server.ram_mb
    server.flavor = flavor.id
    server.flavor_name = flavor.name
    server.vcpus = flavor.vcpus
    server.ram_mb = flavor.ram_mb
    server.disk_gb = flavor.disk_gb
    server.status = "RESIZE"
    db.commit()
    clients.cast_to_worker("resize", {**server_worker_payload(server),
        "new_vcpus": flavor.vcpus, "new_ram_mb": flavor.ram_mb})
    db.refresh(server)
    return {**obj_to_dict(server), "new_flavor": flavor.name}


@router.post("/servers/{ref}/confirmResize")
def confirm_resize(ref: str, _: CurrentUser, db: DB):
    """Confirm a completed resize, clearing the rollback snapshot."""
    server = require_server(db, ref)
    if server.status not in ("VERIFY_RESIZE", "RESIZE", "ACTIVE"):
        raise HTTPException(status_code=409, detail=f"server is not in VERIFY_RESIZE status (current: {server.status})")
    # Clear backup state
    server.original_flavor = None
    server.original_vcpus = None
    server.original_ram_mb = None
    server.status = "ACTIVE"
    db.commit()
    db.refresh(server)
    return obj_to_dict(server)


@router.post("/servers/{ref}/revertResize")
def revert_resize(ref: str, _: CurrentUser, db: DB):
    """Revert a resize, restoring the original flavor."""
    server = require_server(db, ref)
    if not server.original_flavor:
        raise HTTPException(status_code=409, detail="no resize snapshot found for this server")
    original_flavor = db.get(Flavor, server.original_flavor)
    # Restore original flavor resources
    server.flavor = server.original_flavor
    if original_flavor:
        server.flavor_name = original_flavor.name
    server.vcpus = server.original_vcpus
    server.ram_mb = server.original_ram_mb
    server.original_flavor = None
    server.original_vcpus = None
    server.original_ram_mb = None
    server.status = "RESIZE"
    db.commit()
    # Cast revert to worker (hotplug rollback)
    clients.cast_to_worker("resize", {**server_worker_payload(server),
        "new_vcpus": server.vcpus, "new_ram_mb": server.ram_mb})
    server.status = "ACTIVE"
    db.commit()
    db.refresh(server)
    return obj_to_dict(server)


@router.post("/servers/{ref}/rebuild")
def rebuild_server(ref: str, payload: ServerRebuildIn, _: CurrentUser, db: DB):
    server = require_server(db, ref)
    image = clients.api_get(f"{IMAGE_API_URL}/images/{payload.image}")
    server.image_id = image["id"]
    server.status = "REBUILD"
    db.commit()
    clients.cast_to_worker("rebuild", {**server_worker_payload(server), "image": image})
    db.refresh(server)
    return obj_to_dict(server)


@router.post("/servers/{ref}/rescue")
def rescue_server(ref: str, payload: ServerRescueIn, _: CurrentUser, db: DB):
    server = require_server(db, ref)
    rescue_image_id = server.image_id
    if payload.image:
        rescue_img = clients.api_get(f"{IMAGE_API_URL}/images/{payload.image}")
        rescue_image_id = rescue_img["id"]
    server.status = "RESCUE"
    db.commit()
    clients.cast_to_worker("rescue", {**server_worker_payload(server), "rescue_image_id": rescue_image_id})
    db.refresh(server)
    return obj_to_dict(server)


@router.post("/servers/{ref}/unrescue")
def unrescue_server(ref: str, _: CurrentUser, db: DB):
    server = require_server(db, ref)
    server.status = "UNRESCUING"
    db.commit()
    clients.cast_to_worker("unrescue", server_worker_payload(server))
    db.refresh(server)
    return obj_to_dict(server)


@router.post("/servers/{ref}/password")
def set_server_password(ref: str, payload: ServerPasswordIn, _: CurrentUser, db: DB):
    server = require_server(db, ref)
    clients.cast_to_worker("set_password", {**server_worker_payload(server), "password": payload.password})
    return {"server": server.name, "status": "password injection requested"}


@router.delete("/servers/{ref}")
def delete_server(ref: str, current: CurrentUser, db: DB, keep_root_volume: bool = False):
    server = require_server(db, ref)
    # Locked guard
    if server.locked and current.get("role") != "admin":
        raise HTTPException(status_code=409, detail="server is locked; unlock it before deleting")
    original_name = server.name
    # Remove from server group if member
    if server.server_group_id:
        sg = db.get(ServerGroup, server.server_group_id)
        if sg:
            members = [m for m in (sg.members or "").split(",") if m and m != server.id]
            sg.members = ",".join(members) or None
    server.status = "DELETING"
    db.commit()
    # Cast delete to worker — worker will:
    # 1. Destroy libvirt domain
    # 2. Cleanup ports and volumes via HTTP
    # 3. Call conductor to mark DELETED or hard-delete
    clients.cast_to_worker("delete_instance", {
        **server_worker_payload(server),
        "_original_name": original_name,
        "_keep_root_volume": keep_root_volume,
        "_root_volume_id": server.root_volume_id,
        "_port_id": server.port_id,
        "_extra_port_ids": server.extra_port_ids,
        "_block_store_url": BLOCK_STORE_API_URL,
        "_networking_url": NETWORKING_API_URL,
    })
    try:
        clients._rpc.publish_event("compute.instance.delete.start", {
            "server_id":  server.id,
            "name":       original_name,
            "project_id": server.project_id,
            "user_id":    current.get("user_id") or current.get("sub"),
            "status":     "DELETING",
        })
    except Exception as exc:
        log.warning("compute.instance.delete.start event not published: %s", exc)
    return {"status": "deleting", "id": server.id, "name": original_name}


@router.post("/servers/{ref}/live-migrate", status_code=202)
def live_migrate_server(ref: str, payload: dict, _: CurrentUser, db: DB):
    """Live migrate a server to a target compute host (Phase 6.1)."""
    server = require_server(db, ref)
    target_host = payload.get("target_host")
    if not target_host:
        raise HTTPException(status_code=400, detail="target_host is required")
    if server.status != "ACTIVE":
        raise HTTPException(status_code=409, detail=f"server must be ACTIVE to live-migrate; current status is {server.status}")
    
    server.status = "MIGRATING"
    db.commit()
    clients.cast_to_worker("live_migrate_instance", {
        **server_worker_payload(server),
        "target_host": target_host,
    })
    db.refresh(server)
    return {"server_id": server.id, "target_host": target_host, "status": "MIGRATING"}


@router.get("/servers/{ref}/migration-candidates")
def list_migration_candidates(ref: str, _: CurrentUser, db: DB):
    """List possible target hosts for live migration."""
    server = require_server(db, ref)
    try:
        r = requests.get(
            f"{PLACEMENT_API_URL}/compute_nodes",
            headers={"Authorization": f"Bearer {_get_svc_token() or ''}"},
            timeout=10,
        )
        if r.status_code == 200:
            nodes = r.json().get("compute_nodes", [])
            # Exclude current host
            current_host = server.host or "single-node"
            candidates = [n for n in nodes if n.get("host") != current_host]
            return {"current_host": current_host, "candidates": candidates}
    except Exception as e:
        log.warning("Failed to get migration candidates: %s", e)
    return {"current_host": server.host or "single-node", "candidates": []}


@router.post("/servers/{ref}/snapshots", status_code=201)
def create_server_snapshot(ref: str, payload: ServerSnapshotIn, db: DB):
    server = require_server(db, ref)
    if not server.root_volume_id:
        raise HTTPException(status_code=409, detail="server has no root volume")
    snap = clients.block_post(f"/volumes/{server.root_volume_id}/snapshots", {"name": f"{payload.name}-root"})
    image = clients.api_post(
        f"{IMAGE_API_URL}/images/import-path",
        {"name": payload.name, "path": snap["path"], "disk_format": "qcow2", "container_format": "bare", "visibility": "private"},
    )
    return {"server": server.name, "snapshot": snap, "image": image}


@router.post("/servers/{ref}/ports")
def add_server_port(ref: str, payload: ServerPortIn, db: DB):
    server = require_server(db, ref)
    if server.status != "ACTIVE":
        raise HTTPException(status_code=409, detail=f"server must be ACTIVE to add a port; current status is {server.status}")
    port = clients.api_get(f"{NETWORKING_API_URL}/ports/{payload.port}")
    if port["id"] in [server.port_id, *comma_list(server.extra_port_ids)]:
        raise HTTPException(status_code=409, detail="port is already attached to server metadata")
    # Update DB immediately, cast hotplug to worker
    server.extra_port_ids = format_comma_list([*comma_list(server.extra_port_ids), port["id"]])
    db.commit()
    clients.cast_to_worker("attach_port", {**server_worker_payload(server), "port": port})
    db.refresh(server)
    return {"status": "attaching", "server_id": server.id, "port_id": port["id"]}


@router.delete("/servers/{ref}/ports/{port_ref}")
def remove_server_port(ref: str, port_ref: str, db: DB):
    server = require_server(db, ref)
    extra = comma_list(server.extra_port_ids)
    port = clients.api_get(f"{NETWORKING_API_URL}/ports/{port_ref}")
    if port["id"] not in extra:
        raise HTTPException(status_code=404, detail="port is not an attached secondary port")
    server.extra_port_ids = format_comma_list([item for item in extra if item != port["id"]])
    db.commit()
    clients.cast_to_worker("detach_port", {**server_worker_payload(server), "port": port})
    db.refresh(server)
    return {"status": "detaching", "server_id": server.id, "port_id": port["id"]}


@router.post("/servers/{ref}/volumes/attach")
def attach_volume(ref: str, payload: VolumeAttachIn, db: DB):
    server = require_server(db, ref)
    if server.status != "ACTIVE":
        raise HTTPException(status_code=409, detail=f"server must be ACTIVE to attach volume; current status is {server.status}")
    clients.cast_to_worker("attach_volume", {**server_worker_payload(server), "volume": payload.model_dump()})
    return {"status": "attaching", "server_id": server.id, "volume_id": payload.volume_id}


@router.post("/servers/{ref}/volumes/detach")
def detach_volume(ref: str, payload: VolumeAttachIn, db: DB):
    server = require_server(db, ref)
    if server.status not in {"ACTIVE", "SHUTOFF"}:
        raise HTTPException(status_code=409, detail=f"server must be ACTIVE or SHUTOFF to detach volume; current status is {server.status}")
    clients.cast_to_worker("detach_volume", {**server_worker_payload(server), "volume": payload.model_dump()})
    return {"status": "detaching", "server_id": server.id, "volume_id": payload.volume_id}
