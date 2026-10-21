from fastapi import APIRouter, HTTPException, Query
from shared.domain import visible_projects
from shared.auth_middleware import CurrentUser, InternalAuth, UserOrInternal
from sqlalchemy import select

from .. import clients
from ..config import block_store_api_url, image_api_url, networking_api_url, log
from ..db import DB
from ..lookups import (
    free_deleted_server_name,
    get_active_server_by_name,
    require_flavor,
    require_keypair,
    require_server,
    server_port_name,
)
from ..models import Server, ServerGroup, ServerTag, new_id
from ..schemas import ServerIn, ServerUserDataIn
from ..serializers import obj_to_dict, server_to_dict
from ..service import (
    _check_project_quota,
    claim_host,
    default_user_data,
    flavor_snapshot,
    format_comma_list,
    release_allocation,
    fallback_host,
    record_action,
    selected_backend,
    server_metadata_payload,
    server_worker_payload,
)

router = APIRouter()


@router.post("/servers", status_code=201)
def create_server(payload: ServerIn, current: UserOrInternal, db: DB):
    if get_active_server_by_name(db, payload.name):
        raise HTTPException(status_code=409, detail="server already exists")
    if bool(payload.image) == bool(payload.boot_volume):
        raise HTTPException(status_code=400, detail="create server requires exactly one boot source: --image or --boot-volume")
    free_deleted_server_name(db, payload.name)
    backend = selected_backend(payload.backend)
    keypair_ref = payload.key_name or payload.keypair
    keypair = require_keypair(db, keypair_ref) if keypair_ref else None
    flavor = require_flavor(db, payload.flavor)
    _check_project_quota(db, current["project_id"], flavor.vcpus, flavor.ram_mb)
    sg = None
    if payload.server_group:
        sg = db.get(ServerGroup, payload.server_group) or db.scalar(
            select(ServerGroup).where(ServerGroup.name == payload.server_group,
                                       ServerGroup.project_id == current["project_id"]))
        if not sg:
            raise HTTPException(status_code=404, detail="server group not found")
    networking_url, block_store_url = networking_api_url(), block_store_api_url()
    image_url = image_api_url() if payload.image else None
    network = clients.api_get(f"{networking_url}/networks/{payload.network}")
    server_id = new_id()
    root_volume = None
    auto_root_volume = False
    image_id = ""
    boot_source = "volume"
    created_ports: list[dict] = []
    project_id = current["project_id"]
    claimed = False
    try:
        host = claim_host(db, server_id, project_id, flavor, group=sg, az=payload.availability_zone)
        claimed = host is not None
        if host is None:
            host = fallback_host(db, flavor, group=sg, az=payload.availability_zone)
        if payload.image:
            image = clients.api_get(f"{image_url}/images/{payload.image}")
            image_id = image["id"]
            # EC2-style block device mapping: the root volume size is chosen
            # entirely by the user (default 8 GiB) — flavors are instance-type
            # profiles (vCPU/RAM) and no longer dictate storage size.
            root_size_gb = payload.volume_size if payload.volume_size is not None else 8
            if root_size_gb < 1:
                raise HTTPException(status_code=400, detail="root volume size must be at least 1 GB")
            root_volume = clients.block_post(
                "/internal/volumes",
                {
                    "name": f"{payload.name}-root-{server_id[:8]}",
                    "size_gb": root_size_gb,
                    "image_id": image["id"],
                    "image_ref": image["id"],
                    "bootable": True,
                    "source_type": "image",
                    "volume_type": payload.volume_type,
                    "project_id": project_id,
                },
            )
            boot_source = "image"
            auto_root_volume = True
        else:
            root_volume = clients.block_get(f"/volumes/{payload.boot_volume}")
            if not root_volume.get("bootable"):
                raise HTTPException(status_code=400, detail="boot volume must be bootable=true")
            if root_volume.get("status") != "available":
                raise HTTPException(status_code=409, detail=f"boot volume must be available; current status is {root_volume.get('status')}")
            image_id = root_volume.get("image_id") or ""
            boot_source = "volume"

        network_refs = [payload.network, *payload.networks]
        seen_networks = []
        for item in network_refs:
            if item and item not in seen_networks:
                seen_networks.append(item)
        ports = []
        for index, network_ref in enumerate(seen_networks):
            net = network if index == 0 else clients.api_get(f"{networking_url}/networks/{network_ref}")
            port = clients.api_post(
                f"{networking_url}/ports",
                {
                    "name": server_port_name(payload.name, server_id) if index == 0 else f"{payload.name}-{server_id[:8]}-port{index}",
                    "network": net["id"],
                    "device_id": server_id,
                    "device_owner": "compute:instance",
                    "security_group_ids": payload.security_groups,
                },
            )
            ports.append(port)
            created_ports.append(port)
        port = ports[0]
        snapshot = flavor_snapshot(flavor)
        server = Server(
            id=server_id,
            host=host,
            server_group_id=sg.id if sg else None,
            name=payload.name,
            image_id=image_id,
            root_volume_id=root_volume["id"],
            boot_source=boot_source,
            bootable=True,
            flavor=flavor.name,
            flavor_name=snapshot["flavor_name"],
            vcpus=snapshot["vcpus"],
            ram_mb=snapshot["ram_mb"],
            disk_gb=root_volume.get("size_gb") or snapshot["disk_gb"],
            network_id=network["id"],
            port_id=port["id"],
            extra_port_ids=format_comma_list([p["id"] for p in ports[1:]]),
            hostname=payload.name,
            user_data=payload.user_data,
            vendor_data="{}",
            keypair_name=keypair.name if keypair else None,
            public_key_snapshot=keypair.public_key if keypair else None,
            project_id=project_id,
            backend=backend,
            mac_address=port.get("mac_address"),
            ip_address=port.get("fixed_ip") or port.get("ip_address"),
            status="BUILD",
        )
        _check_project_quota(db, project_id, flavor.vcpus, flavor.ram_mb)  # again, under the domain lock the commit below releases
        db.add(server)
        db.commit()
        db.refresh(server)
        record_action(db, server.id, "create", current)
        # ── Async via RabbitMQ ──────────────────────────────────────────────
        # Cast build_instance to compute-worker queue. Worker will:
        #   1. Spawn VM (libvirt)
        #   2. Call conductor to update server fields (host, mac, etc.)
        #   3. Call block-store to attach root volume
        #   4. Call conductor to set status ACTIVE
        worker_payload = server_worker_payload(server, port, ports, root_volume)
        worker_payload["_root_volume_id"] = root_volume["id"]
        worker_payload["_block_store_url"] = block_store_url
        clients.cast_to_worker("build_instance", worker_payload)
        # Emit OpenStack-style event so subscribers (dashboard, monitoring) know a build started
        try:
            clients._rpc.publish_event("compute.instance.create.start", {
                "server_id": server.id,
                "name":      server.name,
                "project_id": server.project_id,
                "user_id":   current.get("sub"),
                "flavor":    server.flavor,
                "status":    "BUILD",
            })
        except Exception as exc:
            log.warning("compute.instance.create.start event not published: %s", exc)
        # API returns immediately with status=BUILD

    except Exception as exc:
        if claimed:
            release_allocation(server_id)
        for created in created_ports:
            clients.api_delete(f"{networking_url}/ports/{created['id']}")
        if auto_root_volume and root_volume:
            clients.block_delete(f"/volumes/{root_volume['id']}")
        existing = db.get(Server, server_id)
        if existing:
            existing.status = "ERROR"
            existing.error_message = str(exc)
            db.commit()
            db.refresh(existing)
            return obj_to_dict(existing)
        raise
    db.commit()
    db.refresh(server)
    return server_to_dict(server)


@router.get("/servers")
def list_servers(
    current: UserOrInternal, db: DB,
    limit: int = Query(default=200, ge=1, le=1000),
    marker: str | None = None,
    tags: str | None = None,
    server_group_id: str | None = None,
):
    q = select(Server).where(Server.status != "DELETED")
    ids = visible_projects(current)
    if ids is not None:
        q = q.where(Server.project_id.in_(ids))
    if server_group_id:
        q = q.where(Server.server_group_id == server_group_id)
    if tags:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        for tag in tag_list:
            tagged_server_ids = db.scalars(
                select(ServerTag.server_id).where(ServerTag.tag == tag)
            ).all()
            q = q.where(Server.id.in_(tagged_server_ids))
    if marker:
        ref = db.get(Server, marker)
        if ref:
            q = q.where(Server.created_at > ref.created_at)
    q = q.order_by(Server.created_at).limit(limit)
    return {"items": [server_to_dict(s) for s in db.scalars(q).all()]}


@router.get("/servers/metadata-lookup", dependencies=[InternalAuth])
def metadata_lookup(
    db: DB,
    instance_id: str | None = None,
    fixed_ip: str | None = None,
    port_id: str | None = None,
):
    query = select(Server).where(Server.status != "DELETED")
    if instance_id:
        query = query.where(Server.id == instance_id)
    elif fixed_ip:
        query = query.where(Server.ip_address == fixed_ip)
    elif port_id:
        query = query.where(Server.port_id == port_id)
    else:
        raise HTTPException(status_code=400, detail="instance_id, fixed_ip, or port_id is required")
    # Order by most recently created to ensure deterministic results when multiple
    # servers share the same IP (e.g. after repeated lab cleanup/create cycles).
    query = query.order_by(Server.created_at.desc())
    server = db.scalar(query)
    if not server:
        raise HTTPException(status_code=404, detail="server metadata not found")
    return server_metadata_payload(server)


@router.get("/internal/servers/by-instance/{instance_id}", dependencies=[InternalAuth])
def internal_server_by_instance(instance_id: str, db: DB):
    server = db.scalar(select(Server).where(Server.id == instance_id, Server.status != "DELETED").order_by(Server.created_at.desc()))
    if not server:
        raise HTTPException(status_code=404, detail="server metadata not found")
    return server_metadata_payload(server)


@router.get("/internal/servers/by-port/{port_id}", dependencies=[InternalAuth])
def internal_server_by_port(port_id: str, db: DB):
    server = db.scalar(select(Server).where(Server.port_id == port_id, Server.status != "DELETED").order_by(Server.created_at.desc()))
    if not server:
        raise HTTPException(status_code=404, detail="server metadata not found")
    return server_metadata_payload(server)


@router.get("/internal/servers/by-ip/{fixed_ip}", dependencies=[InternalAuth])
def internal_server_by_ip(fixed_ip: str, db: DB):
    server = db.scalar(select(Server).where(Server.ip_address == fixed_ip, Server.status != "DELETED").order_by(Server.created_at.desc()))
    if not server:
        raise HTTPException(status_code=404, detail="server metadata not found")
    return server_metadata_payload(server)


@router.get("/servers/{ref}")
def show_server(ref: str, current: UserOrInternal, db: DB):
    return server_to_dict(require_server(db, ref, current))


@router.get("/servers/{ref}/metadata")
def server_metadata(ref: str, current: CurrentUser, db: DB):
    return server_metadata_payload(require_server(db, ref, current))


@router.get("/servers/{ref}/user-data")
def server_user_data(ref: str, current: CurrentUser, db: DB):
    server = require_server(db, ref, current)
    return {"server": server.name, "user_data": server.user_data or default_user_data(server)}


@router.put("/servers/{ref}/user-data")
def set_server_user_data(ref: str, payload: ServerUserDataIn, current: CurrentUser, db: DB):
    server = require_server(db, ref, current)
    server.user_data = payload.user_data
    db.commit()
    db.refresh(server)
    return {"server": server.name, "user_data": server.user_data}
