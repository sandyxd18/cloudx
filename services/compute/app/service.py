import json
import logging
import os
import shutil
from shared.domain import check_domain_caps
from datetime import datetime

import requests
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import clients
from .clients import response_detail
from shared.catalog import CatalogError
from shared.internal import internal_headers
from .config import (
    AUTH_API_URL,
    COMPUTE_BACKEND,
    COMPUTE_HOSTS,
    networking_api_url,
    ONLY_BACKEND,
    placement_api_url,
    log,
)
from .models import Aggregate, AggregateHost, ComputeService, Flavor, Server, ServerAction, ServerGroup
from .serializers import obj_to_dict


def comma_list(value: str | None) -> list[str]:
    return [item for item in (value or "").split(",") if item]


def format_comma_list(values: list[str]) -> str | None:
    return ",".join(dict.fromkeys([item for item in values if item])) or None


def placement_inventory(db: Session) -> dict:
    active = db.scalars(select(Server).where(Server.status.in_({"BUILD", "ACTIVE", "SHUTOFF", "REBOOT"}))).all()
    default_disk = shutil.disk_usage("/").total // (1024 ** 3)
    total_vcpu = int(os.getenv("PLACEMENT_TOTAL_VCPU", str(os.cpu_count() or 1)))
    total_ram = int(os.getenv("PLACEMENT_TOTAL_RAM_MB", "8192"))
    total_disk = int(os.getenv("PLACEMENT_TOTAL_DISK_GB", str(default_disk)))
    used_vcpu = sum(server.vcpus or 0 for server in active)
    used_ram = sum(server.ram_mb or 0 for server in active)
    used_disk = sum(server.disk_gb or 0 for server in active)
    return {
        "total_vcpu": total_vcpu,
        "used_vcpu": used_vcpu,
        "free_vcpu": max(total_vcpu - used_vcpu, 0),
        "total_ram_mb": total_ram,
        "used_ram_mb": used_ram,
        "free_ram_mb": max(total_ram - used_ram, 0),
        "total_disk_gb": total_disk,
        "used_disk_gb": used_disk,
        "free_disk_gb": max(total_disk - used_disk, 0),
    }


def compute_hosts() -> list[str]:
    """Compute nodes that can take a new server: COMPUTE_HOSTS, else the providers registered in placement."""
    if COMPUTE_HOSTS:
        return COMPUTE_HOSTS
    try:
        response = requests.get(f"{placement_api_url()}/resource_providers", headers=internal_headers(), timeout=5)
        response.raise_for_status()
        body = response.json()
        return [rp["name"] for rp in (body.get("resource_providers", body) if isinstance(body, dict) else body)]
    except (requests.RequestException, CatalogError, ValueError, KeyError) as exc:
        log.warning("cannot list compute nodes from placement (%s): new servers go to any worker", exc)
        return []


def schedule_host(db: Session, hosts: list[str] | None = None) -> str | None:
    """Compute node with the fewest servers (None = no node known, the shared queue picks one)."""
    hosts = compute_hosts() if hosts is None else hosts
    if not hosts:
        return None
    counts = dict(db.execute(
        select(Server.host, func.count()).where(Server.host.in_(hosts), Server.status != "DELETED").group_by(Server.host)
    ).all())
    return min(hosts, key=lambda h: counts.get(h, 0))  # min() keeps the first host on ties


def _constraints(db: Session, flavor, group=None, az=None, host=None, avoid=()) -> tuple[list[str], list[str] | None]:
    """(exclude, only) host lists from disabled services, AZ, flavor extra_specs vs aggregate metadata, server group."""
    exclude = set(avoid) | {s.host for s in db.scalars(select(ComputeService)) if s.disabled or s.forced_down}
    only = set(COMPUTE_HOSTS) if COMPUTE_HOSTS else None
    wanted = {k.split(":", 1)[1]: v for k, v in json.loads(flavor.extra_specs or "{}").items()
              if k.startswith("aggregate_instance_extra_specs:")}
    if az or wanted:
        aggs = db.scalars(select(Aggregate)).all()
        if az:
            aggs = [a for a in aggs if a.availability_zone == az]
            if not aggs:
                raise HTTPException(status_code=400, detail=f"unknown availability zone {az}")
        for k, v in wanted.items():
            aggs = [a for a in aggs if str(json.loads(a.extra_metadata or "{}").get(k)) == str(v)]
        ids = {a.id for a in aggs}
        in_aggs = {h for (h,) in db.execute(select(AggregateHost.host).where(AggregateHost.aggregate_id.in_(ids)))}
        only = in_aggs if only is None else only & in_aggs
    if group:
        used = [h for (h,) in db.execute(select(Server.host).where(
            Server.server_group_id == group.id, Server.status != "DELETED", Server.host.is_not(None)))]
        if group.policy == "anti-affinity":
            exclude |= set(used)
        elif group.policy == "affinity" and used:
            only = {used[0]} if only is None else only & {used[0]}
    if host:
        only = {host} if only is None else only & {host}
    if only is not None:
        only -= exclude
        if not only:
            raise HTTPException(status_code=409, detail="No valid host was found")
    return sorted(exclude), None if only is None else sorted(only)


def fallback_host(db: Session, flavor, **kw) -> str | None:
    """Least-servers choice that still honours the filters (used when placement cannot decide)."""
    exclude, only = _constraints(db, flavor, **kw)
    hosts = [h for h in (only if only is not None else compute_hosts()) if h not in exclude]
    return schedule_host(db, hosts) if hosts else None


def claim_host(db: Session, server_id: str, project_id: str, flavor: Flavor, disk_gb: int = 0,
               group: "ServerGroup | None" = None, az: str | None = None, host: str | None = None,
               avoid: tuple = ()) -> str | None:
    """Placement picks a node and records the allocation. Returns the host, or None to use the legacy
    least-servers path (placement down, or no node has reported inventory yet)."""
    exclude, only = _constraints(db, flavor, group, az, host, avoid)
    resources = {"VCPU": flavor.vcpus, "MEMORY_MB": flavor.ram_mb}
    try:
        r = requests.post(f"{placement_api_url()}/internal/claim", headers=internal_headers(), timeout=10, json={
            "consumer_uuid": server_id, "project_id": project_id, "resources": resources,
            "exclude_hosts": exclude, "only_hosts": only})
    except (requests.RequestException, CatalogError) as exc:
        log.warning("placement unreachable (%s): falling back to least-servers scheduling", exc)
        return None
    if r.status_code == 503:
        return None
    if r.status_code == 409:
        raise HTTPException(status_code=409, detail={"message": "No valid host was found", "detail": response_detail(r)})
    r.raise_for_status()
    return r.json()["host"]


def release_allocation(server_id: str) -> None:
    try:
        requests.delete(f"{placement_api_url()}/internal/allocations/{server_id}", headers=internal_headers(), timeout=5)
    except (requests.RequestException, CatalogError) as exc:
        log.warning("could not release placement allocation of %s: %s", server_id, exc)


def selected_backend(requested: str | None) -> str:
    backend = (requested or COMPUTE_BACKEND or ONLY_BACKEND).lower()
    if backend == "simulator":
        raise HTTPException(status_code=400, detail="simulator backend has been removed; only libvirt is supported")
    if backend != ONLY_BACKEND:
        raise HTTPException(status_code=400, detail=f"unsupported compute backend '{backend}'; only libvirt is supported")
    return ONLY_BACKEND


def flavor_snapshot(flavor: Flavor) -> dict:
    return {
        "flavor_name": flavor.name,
        "vcpus": flavor.vcpus,
        "ram_mb": flavor.ram_mb,
        "disk_gb": flavor.disk_gb,
        "ephemeral_gb": flavor.ephemeral_gb,
        "swap_mb": flavor.swap_mb,
        "is_public": flavor.is_public,
    }


def server_worker_payload(server: Server, port: dict | None = None, ports: list[dict] | None = None, root_volume: dict | None = None) -> dict:
    payload = obj_to_dict(server)
    payload["flavor_snapshot"] = {
        "name": server.flavor_name or server.flavor,
        "vcpus": server.vcpus,
        "ram_mb": server.ram_mb,
        "disk_gb": server.disk_gb,
    }
    if port:
        payload["port"] = port
    if ports:
        payload["ports"] = ports
    if root_volume:
        payload["root_volume"] = root_volume
    return jsonable_encoder(payload)


def update_server_from_worker(server: Server, result: dict) -> None:
    for field in (
        "host",
        "instance_dir",
        "disk_path",
        "config_drive_path",
        "libvirt_domain_name",
        "tap_name",
        "mac_address",
        "ip_address",
    ):
        if field in result:
            setattr(server, field, result[field])


def default_user_data(server: Server) -> str:
    lines = ["#cloud-config", "package_update: false", "ssh_pwauth: false"]
    if server.public_key_snapshot:
        lines.extend(["ssh_authorized_keys:", f"  - {server.public_key_snapshot}"])
    return "\n".join(lines) + "\n"


def server_metadata_payload(server: Server) -> dict:
    public_keys = {}
    if server.keypair_name and server.public_key_snapshot:
        public_keys[server.keypair_name] = server.public_key_snapshot
    try:
        port = clients.api_get(f"{networking_api_url()}/ports/{server.port_id}")
    except Exception as exc:
        log.warning("could not fetch the port for server metadata: %s", exc)
        port = {}
    return {
        "uuid": server.id,
        "hostname": server.hostname or server.name,
        "name": server.name,
        "public_keys": public_keys,
        "meta": {},
        "devices": [],
        "instance_id": server.id,
        "local_hostname": server.hostname or server.name,
        "instance_type": server.flavor_name or server.flavor,
        "port_id": server.port_id,
        "fixed_ip": server.ip_address,
        "network_id": server.network_id,
        "mac_address": server.mac_address,
        "created_at": server.created_at.isoformat() if server.created_at else None,
        "user_data": server.user_data,
        "vendor_data": server.vendor_data or "{}",
        "network": port,
    }


LIVE_STATES = {"BUILD", "ACTIVE", "SHUTOFF", "REBOOT", "PAUSED", "SUSPENDED", "RESCUE", "RESIZE", "VERIFY_RESIZE"}


def _check_project_quota(db: Session, project_id: str, requested_vcpus: int, requested_ram_mb: int):
    """Enforce per-project limits from auth-api against what this project's live servers already use.

    Usage comes from compute's own servers table (placement allocations are not written by compute).
    ponytail: if auth-api is unreachable the check is skipped (logged), not failed.
    It runs twice per create: early (fail fast) and again just before the insert, so the domain advisory lock
    is held until that insert commits. The early check alone would let parallel creates both pass.
    """
    try:
        resp = requests.get(f"{AUTH_API_URL}/internal/projects/{project_id}/quota", headers=internal_headers(), timeout=5)
        resp.raise_for_status()
        quota = resp.json()
    except (requests.RequestException, ValueError) as exc:
        logging.getLogger("compute-api").warning("Quota check skipped (auth-api): %s", exc)
        return
    live = db.scalars(select(Server).where(
        Server.project_id == project_id, Server.status.in_(LIVE_STATES))).all()
    checks = (
        ("max_instances", len(live) + 1, quota.get("max_instances", 10)),
        ("max_vcpus", sum(s.vcpus or 0 for s in live) + requested_vcpus, quota.get("max_vcpus", 20)),
        ("max_ram_mb", sum(s.ram_mb or 0 for s in live) + requested_ram_mb, quota.get("max_ram_mb", 32768)),
    )
    for name, wanted, limit in checks:
        if wanted > limit:
            raise HTTPException(status_code=413, detail=f"Quota exceeded: {name} ({limit}) reached")
    live_in = lambda ids: Server.project_id.in_(ids) & Server.status.in_(LIVE_STATES)  # noqa: E731
    total = lambda col: lambda ids: db.scalar(select(func.coalesce(func.sum(col), 0)).where(live_in(ids)))  # noqa: E731
    check_domain_caps(db, quota.get("domain"), {
        "max_instances": (lambda ids: db.scalar(select(func.count()).select_from(Server).where(live_in(ids))), 1),
        "max_vcpus": (total(Server.vcpus), requested_vcpus),
        "max_ram_mb": (total(Server.ram_mb), requested_ram_mb),
    })


def record_action(db: Session, server_id: str, action: str, current: dict, result: str | None = None, message: str | None = None, finish: bool = False) -> ServerAction:
    now_dt = datetime.utcnow()
    sa_row = ServerAction(
        server_id=server_id,
        action=action,
        user_id=current.get("sub"),
        project_id=current.get("project_id"),
        start_time=now_dt,
        result=result if finish else None,
        message=message if finish else None,
        finish_time=now_dt if finish else None,
    )
    db.add(sa_row)
    db.commit()
    return sa_row


def finish_action(db: Session, server_id: str, result: str = "success", message: str | None = None, action: str | None = None) -> None:
    stmt = select(ServerAction).where(ServerAction.server_id == server_id, ServerAction.finish_time.is_(None))
    if action:
        stmt = stmt.where(ServerAction.action == action)
    stmt = stmt.order_by(ServerAction.start_time.desc())
    row = db.scalar(stmt)
    if row:
        row.result = result
        row.message = message
        row.finish_time = datetime.utcnow()
        db.commit()


def project_limits(db: Session, project_id: str) -> dict:
    """Nova-style absolute limits: quota from auth-api (None if unreachable) + usage from the servers table."""
    try:
        resp = requests.get(f"{AUTH_API_URL}/internal/projects/{project_id}/quota", headers=internal_headers(), timeout=5)
        resp.raise_for_status()
        quota = resp.json()
    except (requests.RequestException, ValueError):
        quota = {}
    live = db.scalars(select(Server).where(Server.project_id == project_id, Server.status.in_(LIVE_STATES))).all()
    return {"absolute": {
        "maxTotalInstances": quota.get("max_instances"), "totalInstancesUsed": len(live),
        "maxTotalCores": quota.get("max_vcpus"), "totalCoresUsed": sum(s.vcpus or 0 for s in live),
        "maxTotalRAMSize": quota.get("max_ram_mb"), "totalRAMUsed": sum(s.ram_mb or 0 for s in live)}}
