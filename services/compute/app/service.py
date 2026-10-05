import logging
import os
import shutil

import requests
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import clients
from .clients import _svc_headers
from .config import (
    AUTH_API_URL,
    COMPUTE_BACKEND,
    COMPUTE_HOSTS,
    NETWORKING_API_URL,
    ONLY_BACKEND,
    PLACEMENT_API_URL,
    log,
)
from .models import Flavor, Server
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
        response = requests.get(f"{PLACEMENT_API_URL}/resource_providers", headers=_svc_headers(), timeout=5)
        response.raise_for_status()
        body = response.json()
        return [rp["name"] for rp in (body.get("resource_providers", body) if isinstance(body, dict) else body)]
    except (requests.RequestException, ValueError, KeyError) as exc:
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


def ensure_capacity(db: Session, flavor: Flavor) -> None:
    inv = placement_inventory(db)
    if flavor.vcpus > inv["free_vcpu"] or flavor.ram_mb > inv["free_ram_mb"] or flavor.disk_gb > inv["free_disk_gb"]:
        raise HTTPException(status_code=409, detail={"message": "Insufficient resources", "inventory": inv})


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
        port = clients.api_get(f"{NETWORKING_API_URL}/ports/{server.port_id}")
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


def _check_project_quota(project_id: str, requested_vcpus: int, requested_ram_mb: int):
    """Enforce per-project quota limits against placement usages."""
    try:
        quota_resp = requests.get(f"{AUTH_API_URL}/projects/{project_id}/quota", headers=_svc_headers(), timeout=5)
        if quota_resp.status_code == 200:
            quota = quota_resp.json()
            usage_resp = requests.get(f"{PLACEMENT_API_URL}/usages?project_id={project_id}", headers=_svc_headers(), timeout=5)
            usages = usage_resp.json().get("usages", {}) if usage_resp.status_code == 200 else {}
            curr_instances = usages.get("instances", 0)
            curr_vcpus = usages.get("VCPU", 0)
            curr_ram = usages.get("MEMORY_MB", 0)

            max_inst = quota.get("max_instances", 10)
            max_vcpu = quota.get("max_vcpus", 20)
            max_ram = quota.get("max_ram_mb", 32768)

            if curr_instances + 1 > max_inst:
                raise HTTPException(status_code=413, detail=f"Quota exceeded: max_instances ({max_inst}) reached")
            if curr_vcpus + requested_vcpus > max_vcpu:
                raise HTTPException(status_code=413, detail=f"Quota exceeded: max_vcpus ({max_vcpu}) reached")
            if curr_ram + requested_ram_mb > max_ram:
                raise HTTPException(status_code=413, detail=f"Quota exceeded: max_ram_mb ({max_ram}) reached")
    except HTTPException:
        raise
    except Exception as exc:
        logging.getLogger("compute-api").warning("Quota check failed/skipped: %s", exc)
