from urllib.parse import quote

import requests
from fastapi import APIRouter, HTTPException
from shared.auth_middleware import AdminOrInternal, CurrentUser, UserOrInternal
from shared.catalog import CatalogError
from shared.internal import internal_headers

from .. import clients
from ..config import (
    block_store_api_url,
    image_api_url,
    networking_api_url,
    placement_api_url,
    log,
)
from ..db import DB
from ..lookups import get_flavor, require_flavor, require_server, require_unlocked_server
from ..models import ServerGroup
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
from ..service import comma_list, format_comma_list, release_allocation, server_worker_payload

router = APIRouter()


@router.post("/servers/{ref}/start")
def start_server(ref: str, current: CurrentUser, db: DB):
    server = require_unlocked_server(db, ref, current)
    server.status = "STARTING"
    db.commit()
    clients.cast_to_worker("start", server_worker_payload(server))
    db.refresh(server)
    return obj_to_dict(server)


@router.post("/servers/{ref}/stop")
def stop_server(ref: str, current: CurrentUser, db: DB):
    server = require_unlocked_server(db, ref, current)
    server.status = "STOPPING"
    db.commit()
    clients.cast_to_worker("stop", server_worker_payload(server))
    db.refresh(server)
    return obj_to_dict(server)


@router.post("/servers/{ref}/reboot")
def reboot_server(ref: str, current: CurrentUser, db: DB, payload: ServerRebootIn = None):
    server = require_unlocked_server(db, ref, current)
    rtype = payload.type if payload else "soft"
    server.status = "REBOOT"
    db.commit()
    clients.cast_to_worker("reboot", {**server_worker_payload(server), "reboot_type": rtype})
    db.refresh(server)
    return obj_to_dict(server)


@router.post("/servers/{ref}/pause")
def pause_server(ref: str, current: CurrentUser, db: DB):
    server = require_unlocked_server(db, ref, current)
    server.status = "PAUSING"
    db.commit()
    clients.cast_to_worker("pause", server_worker_payload(server))
    db.refresh(server)
    return obj_to_dict(server)


@router.post("/servers/{ref}/resume")
def resume_server(ref: str, current: CurrentUser, db: DB):
    server = require_unlocked_server(db, ref, current)
    server.status = "RESUMING"
    db.commit()
    clients.cast_to_worker("resume", server_worker_payload(server))
    db.refresh(server)
    return obj_to_dict(server)


@router.post("/servers/{ref}/suspend")
def suspend_server(ref: str, current: CurrentUser, db: DB):
    server = require_unlocked_server(db, ref, current)
    server.status = "SUSPENDING"
    db.commit()
    clients.cast_to_worker("suspend", server_worker_payload(server))
    db.refresh(server)
    return obj_to_dict(server)


@router.post("/servers/{ref}/unsuspend")
def unsuspend_server(ref: str, current: CurrentUser, db: DB):
    server = require_unlocked_server(db, ref, current)
    if server.status != "SUSPENDED":
        raise HTTPException(status_code=409, detail=f"server must be SUSPENDED to unsuspend; current status is {server.status}")
    server.status = "RESUMING"
    db.commit()
    clients.cast_to_worker("unsuspend", server_worker_payload(server))
    db.refresh(server)
    return obj_to_dict(server)


@router.post("/servers/{ref}/resize")
def resize_server(ref: str, payload: ServerResizeIn, current: CurrentUser, db: DB):
    server = require_unlocked_server(db, ref, current)
    if server.status == "VERIFY_RESIZE":
        raise HTTPException(status_code=409, detail="server has a resize to confirm or revert (confirmResize or revertResize) before it can be resized again")
    if server.status not in ("ACTIVE", "SHUTOFF"):
        raise HTTPException(status_code=409, detail=f"server must be ACTIVE or SHUTOFF to resize; current status is {server.status}")
    flavor = require_flavor(db, payload.flavor)
    # Snapshot original flavor for potential revert
    server.original_flavor = server.flavor
    server.original_vcpus = server.vcpus
    server.original_ram_mb = server.ram_mb
    server.original_status = server.status  # a stopped server must end stopped, or auto-recovery starts it
    server.flavor = flavor.name  # servers.py stores the name here too
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
def confirm_resize(ref: str, current: CurrentUser, db: DB):
    """Accept the new size: the worker leaves a resized server in VERIFY_RESIZE until this (or revertResize) is called.
    A server that an older version left ACTIVE with its rollback snapshot can be confirmed too."""
    server = require_unlocked_server(db, ref, current)
    waiting = server.status == "VERIFY_RESIZE" or (server.status == "ACTIVE" and server.original_flavor)
    if not waiting:
        raise HTTPException(status_code=409, detail=f"server is not in VERIFY_RESIZE status (current: {server.status})")
    server.status = server.original_status or "ACTIVE"
    server.original_flavor = None
    server.original_vcpus = None
    server.original_ram_mb = None
    server.original_status = None
    server.resize_pending_since = None
    db.commit()
    db.refresh(server)
    return obj_to_dict(server)


@router.post("/servers/{ref}/revertResize")
def revert_resize(ref: str, current: CurrentUser, db: DB):
    """Go back to the size before the resize: from VERIFY_RESIZE, from a resize that failed (ERROR), or from one that an
    older version left ACTIVE. The worker does the change and sets ACTIVE."""
    server = require_unlocked_server(db, ref, current)
    if not server.original_flavor:
        raise HTTPException(status_code=409, detail="no resize snapshot found for this server")
    if server.status not in ("VERIFY_RESIZE", "ACTIVE", "ERROR"):
        raise HTTPException(status_code=409, detail=f"server must be in VERIFY_RESIZE status to revert a resize (current: {server.status})")
    # original_flavor is a name, or an id on a server resized before the name was stored
    original_flavor = get_flavor(db, server.original_flavor)
    server.flavor = server.original_flavor
    server.flavor_name = original_flavor.name if original_flavor else server.original_flavor
    server.vcpus = server.original_vcpus
    server.ram_mb = server.original_ram_mb
    target_status = server.original_status or "ACTIVE"
    server.original_flavor = None
    server.original_vcpus = None
    server.original_ram_mb = None
    server.original_status = None
    server.resize_pending_since = None
    server.status = "RESIZE"
    db.commit()
    clients.cast_to_worker("resize", {**server_worker_payload(server), "new_vcpus": server.vcpus,
        "new_ram_mb": server.ram_mb, "_revert": True, "_target_status": target_status})
    db.refresh(server)
    return obj_to_dict(server)


def ports_of(server) -> tuple[dict, list[dict]]:
    """The primary port and every port of the server, primary first. The worker makes one NIC per port and describes
    each in the config drive, so an action that builds the domain again needs the ports, not only their ids."""
    url = networking_api_url()
    port = clients.api_get(f"{url}/ports/{server.port_id}")
    return port, [port] + [clients.api_get(f"{url}/ports/{p}") for p in comma_list(server.extra_port_ids)]


@router.post("/servers/{ref}/rebuild")
def rebuild_server(ref: str, payload: ServerRebuildIn, current: CurrentUser, db: DB):
    server = require_unlocked_server(db, ref, current)
    image = clients.api_get(f"{image_api_url()}/images/{quote(payload.image, safe='')}", caller=current)
    port, ports = ports_of(server)
    server.image_id = image["id"]
    server.status = "REBUILD"
    db.commit()
    clients.cast_to_worker("rebuild", {**server_worker_payload(server, port, ports), "image": image})
    db.refresh(server)
    return obj_to_dict(server)


@router.post("/servers/{ref}/rescue")
def rescue_server(ref: str, payload: ServerRescueIn, current: CurrentUser, db: DB):
    server = require_unlocked_server(db, ref, current)
    rescue_image_id = server.image_id
    if payload.image:
        rescue_img = clients.api_get(f"{image_api_url()}/images/{quote(payload.image, safe='')}", caller=current)
        rescue_image_id = rescue_img["id"]
    port, ports = ports_of(server)
    server.status = "RESCUE"
    db.commit()
    clients.cast_to_worker("rescue", {**server_worker_payload(server, port, ports), "rescue_image_id": rescue_image_id})
    db.refresh(server)
    return obj_to_dict(server)


@router.post("/servers/{ref}/unrescue")
def unrescue_server(ref: str, current: CurrentUser, db: DB):
    server = require_unlocked_server(db, ref, current)
    port, ports = ports_of(server)
    server.status = "UNRESCUING"
    db.commit()
    clients.cast_to_worker("unrescue", server_worker_payload(server, port, ports))
    db.refresh(server)
    return obj_to_dict(server)


@router.post("/servers/{ref}/password")
def set_server_password(ref: str, payload: ServerPasswordIn, current: CurrentUser, db: DB):
    server = require_unlocked_server(db, ref, current)
    clients.cast_to_worker("set_password", {**server_worker_payload(server), "password": payload.password})
    return {"server": server.name, "status": "password injection requested"}


@router.delete("/servers/{ref}")
def delete_server(ref: str, current: UserOrInternal, db: DB, keep_root_volume: bool = False):
    server = require_unlocked_server(db, ref, current)
    original_name = server.name
    try:
        block_store_url, networking_url = block_store_api_url(), networking_api_url()
    except CatalogError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    # Remove from server group if member
    if server.server_group_id:
        sg = db.get(ServerGroup, server.server_group_id)
        if sg:
            members = [m for m in (sg.members or "").split(",") if m and m != server.id]
            sg.members = ",".join(members) or None
    server.status = "DELETING"
    db.commit()
    release_allocation(server.id)
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
        "_block_store_url": block_store_url,
        "_networking_url": networking_url,
    })
    try:
        clients._rpc.publish_event("compute.instance.delete.start", {
            "server_id":  server.id,
            "name":       original_name,
            "project_id": server.project_id,
            "user_id":    current.get("sub"),
            "status":     "DELETING",
        })
    except Exception as exc:
        log.warning("compute.instance.delete.start event not published: %s", exc)
    return {"status": "deleting", "id": server.id, "name": original_name}


@router.post("/servers/{ref}/live-migrate", status_code=202)
def live_migrate_server(ref: str, payload: dict, current: CurrentUser, db: DB):
    """Not built yet. The route stays so the API shape is fixed; it refuses the request and changes nothing.
    POST /servers/{ref}/migrate is the supported way to move a server to another node."""
    require_server(db, ref, current)
    raise HTTPException(
        status_code=501,
        detail="live migration is not supported yet; use POST /servers/{ref}/migrate (cold migrate), which restarts the server on the target node",
    )


@router.get("/servers/{ref}/migration-candidates")
def list_migration_candidates(ref: str, current: CurrentUser, db: DB):
    """List possible target hosts for live migration."""
    server = require_server(db, ref, current)
    try:
        r = requests.get(
            f"{placement_api_url()}/compute_nodes",
            headers=internal_headers(),
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


@router.post("/servers/{ref}/snapshots", status_code=202)
def create_server_snapshot(ref: str, payload: ServerSnapshotIn, current: CurrentUser, db: DB):
    """Image of the server's root volume. Block-store snapshots the volume and its worker uploads the snapshot to
    image-api, so the image appears when that ends; a failure leaves the volume snapshot in `error` with the reason."""
    server = require_server(db, ref, current)
    if not server.root_volume_id:
        raise HTTPException(status_code=409, detail="server has no root volume")
    try:
        image_url = image_api_url()  # resolved before the first side effect: no orphan volume snapshot on failure
    except CatalogError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    try:
        clients.api_get(f"{image_url}/images/{payload.name}")
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
    else:
        raise HTTPException(status_code=409, detail=f"image '{payload.name}' already exists")
    snap = clients.block_post(f"/volumes/{server.root_volume_id}/upload-image",
                              {"image_name": payload.name, "project_id": server.project_id})
    return {"server": server.name, "snapshot": snap, "image": {"name": payload.name, "status": "queued"}}


@router.post("/servers/{ref}/ports")
def add_server_port(ref: str, payload: ServerPortIn, current: CurrentUser, db: DB):
    server = require_unlocked_server(db, ref, current)
    if server.status != "ACTIVE":
        raise HTTPException(status_code=409, detail=f"server must be ACTIVE to add a port; current status is {server.status}")
    port = clients.api_get(f"{networking_api_url()}/ports/{payload.port}")
    if port["id"] in [server.port_id, *comma_list(server.extra_port_ids)]:
        raise HTTPException(status_code=409, detail="port is already attached to server metadata")
    # Update DB immediately, cast hotplug to worker
    server.extra_port_ids = format_comma_list([*comma_list(server.extra_port_ids), port["id"]])
    db.commit()
    clients.cast_to_worker("attach_port", {**server_worker_payload(server), "port": port})
    db.refresh(server)
    return {"status": "attaching", "server_id": server.id, "port_id": port["id"]}


@router.delete("/servers/{ref}/ports/{port_ref}")
def remove_server_port(ref: str, port_ref: str, current: CurrentUser, db: DB):
    server = require_unlocked_server(db, ref, current)
    extra = comma_list(server.extra_port_ids)
    port = clients.api_get(f"{networking_api_url()}/ports/{port_ref}")
    if port["id"] not in extra:
        raise HTTPException(status_code=404, detail="port is not an attached secondary port")
    server.extra_port_ids = format_comma_list([item for item in extra if item != port["id"]])
    db.commit()
    clients.cast_to_worker("detach_port", {**server_worker_payload(server), "port": port})
    db.refresh(server)
    return {"status": "detaching", "server_id": server.id, "port_id": port["id"]}


# Service-to-service (an admin or the internal token, never open): block-store calls these after it has checked the
# volume, its project and the server lock.
@router.post("/servers/{ref}/volumes/attach")
def attach_volume(ref: str, payload: VolumeAttachIn, current: AdminOrInternal, db: DB):
    server = require_unlocked_server(db, ref, current)
    if server.status != "ACTIVE":
        raise HTTPException(status_code=409, detail=f"server must be ACTIVE to attach volume; current status is {server.status}")
    clients.cast_to_worker("attach_volume", {**server_worker_payload(server), "volume": payload.model_dump()})
    return {"status": "attaching", "server_id": server.id, "volume_id": payload.volume_id}


@router.post("/servers/{ref}/volumes/detach")
def detach_volume(ref: str, payload: VolumeAttachIn, current: AdminOrInternal, db: DB):
    server = require_unlocked_server(db, ref, current)
    if server.status not in {"ACTIVE", "SHUTOFF"}:
        raise HTTPException(status_code=409, detail=f"server must be ACTIVE or SHUTOFF to detach volume; current status is {server.status}")
    clients.cast_to_worker("detach_volume", {**server_worker_payload(server), "volume": payload.model_dump()})
    return {"status": "detaching", "server_id": server.id, "volume_id": payload.volume_id}
