"""
metadata_service.py — Core metadata logic for cloudx metadata-api.

Implements:
  - EC2-compatible metadata (latest/)
  - OpenStack metadata (openstack/latest/)
  - network_data.json with multi-NIC support
  - vendor_data.json + vendor_data2.json
  - Instance identity document (OpenStack extension)
  - HMAC-signed instance identity (optional, set METADATA_HMAC_KEY)
"""

import hashlib
import hmac
import ipaddress
import json
import os
import time
from fastapi import HTTPException, Request

from .clients import compute_get
from .config import METADATA_HMAC_KEY


def _first_forwarded_for(value: str | None) -> str | None:
    if not value:
        return None
    return value.split(',')[0].strip() or None


def lookup_metadata(
    request: Request,
    instance_id: str | None = None,
    port_id: str | None = None,
    fixed_ip: str | None = None,
) -> dict:
    query_params = getattr(request, "query_params", {})
    headers = getattr(request, "headers", {})
    client = getattr(request, "client", None)
    query_instance = instance_id or query_params.get("instance_id")
    query_port = port_id or query_params.get("port_id")
    query_ip = fixed_ip or query_params.get("fixed_ip")
    header_instance = headers.get("X-Instance-ID")
    header_port = headers.get("X-Port-ID")
    forwarded_for = _first_forwarded_for(headers.get("X-Forwarded-For"))
    client_ip = client.host if client else None

    # Identity hints are only believed from the metadata-proxy (X-Internal-Token); a VM could forge them.
    token = os.getenv("INTERNAL_TOKEN", "")
    if token and not hmac.compare_digest(headers.get("X-Internal-Token", ""), token):
        header_instance = header_port = query_instance = query_port = query_ip = forwarded_for = None

    candidates = [
        ("instance_id", header_instance),
        ("instance_id", query_instance),
        ("port_id", header_port),
        ("port_id", query_port),
        ("fixed_ip", query_ip),
        ("fixed_ip", forwarded_for),
        ("fixed_ip", client_ip if client_ip not in ("127.0.0.1", "::1", "") else None),
    ]
    for key, value in candidates:
        if not value:
            continue
        try:
            return compute_get("/servers/metadata-lookup", params={key: value})
        except HTTPException as exc:
            if exc.status_code == 404:
                continue
            raise
    raise HTTPException(
        status_code=404,
        detail={"message": "instance metadata not found",
                "lookup": "instance_id, port_id, fixed_ip, headers, or source IP"},
    )


# ── Index responses ────────────────────────────────────────────────────────────

def versions() -> str:
    return "latest\nopenstack\n"


def latest_index() -> str:
    return "meta-data/\nuser-data\n"


def openstack_index() -> str:
    return "latest/\n"


def openstack_latest_index() -> str:
    return (
        "meta_data.json\n"
        "network_data.json\n"
        "user_data\n"
        "vendor_data.json\n"
        "vendor_data2.json\n"
        "password\n"
    )


def meta_data_index() -> str:
    return "instance-id\nlocal-hostname\nhostname\ninstance-type\npublic-keys/\n"


# ── Public key helpers ─────────────────────────────────────────────────────────

def public_keys_index(metadata: dict) -> str:
    keys = list((metadata.get("public_keys") or {}).keys())
    if not keys:
        return ""
    return "\n".join(f"{idx}={name}" for idx, name in enumerate(keys)) + "\n"


def public_key_zero_index(metadata: dict) -> str:
    keys = list((metadata.get("public_keys") or {}).keys())
    if not keys:
        raise HTTPException(status_code=404, detail="no public key for instance")
    return "openssh-key\n"


def first_public_key(metadata: dict) -> str:
    keys = metadata.get("public_keys") or {}
    if not keys:
        raise HTTPException(status_code=404, detail="no public key for instance")
    return next(iter(keys.values())) + "\n"


def default_user_data(metadata: dict) -> str:
    return metadata.get("user_data") or "#cloud-config\npackage_update: false\n"


# ── meta_data.json ─────────────────────────────────────────────────────────────

def meta_data_json(metadata: dict) -> dict:
    return {
        "uuid": metadata["uuid"],
        "name": metadata["name"],
        "hostname": metadata.get("hostname") or metadata["name"],
        "launch_index": 0,
        "availability_zone": "default",
        "project_id": metadata.get("project_id", ""),
        "public_keys": metadata.get("public_keys") or {},
        "meta": metadata.get("meta") or {},
        "devices": metadata.get("devices") or [],
        "instance_type": metadata.get("instance_type") or "unknown",
        "created_at": metadata.get("created_at", ""),
    }


# ── network_data.json — multi-NIC support ─────────────────────────────────────

def _netmask(cidr: str | None) -> str | None:
    if not cidr:
        return None
    try:
        return str(ipaddress.ip_network(cidr, strict=False).netmask)
    except ValueError:
        return None


def _prefix_len(cidr: str | None) -> int | None:
    if not cidr:
        return None
    try:
        return ipaddress.ip_network(cidr, strict=False).prefixlen
    except ValueError:
        return None


def network_data_json(metadata: dict) -> dict:
    """Build network_data.json.

    Supports multiple ports: primary port + any extra_port_ids stored in metadata.
    Each port is represented as a separate link + network entry.
    """
    primary_port = metadata.get("network") or {}
    port_id = metadata.get("port_id") or primary_port.get("id")
    fixed_ip = metadata.get("fixed_ip") or primary_port.get("fixed_ip") or primary_port.get("ip_address")
    mac = metadata.get("mac_address") or primary_port.get("mac_address")
    cidr = primary_port.get("cidr")
    gateway = primary_port.get("gateway_ip")
    dns_list = primary_port.get("dns_nameservers") or []

    links = []
    networks = []
    services = []

    def _add_link_and_network(p_id, p_mac, p_ip, p_cidr, p_gw, p_dns, idx):
        link_id = f"tap{p_id[:8]}" if p_id else f"eth{idx}"
        link = {
            "id": link_id,
            "type": "phy",
            "ethernet_mac_address": p_mac or "",
            "mtu": 1500,
        }
        links.append(link)

        net = {
            "id": f"network{idx}",
            "type": "ipv4",
            "link": link_id,
            "ip_address": p_ip or "",
            "netmask": _netmask(p_cidr),
            "routes": [],
            "network_id": primary_port.get("network_id", "") if idx == 0 else "",
        }
        if p_gw:
            net["routes"].append({"network": "0.0.0.0", "netmask": "0.0.0.0", "gateway": p_gw})
        networks.append(net)

        for dns in (p_dns if isinstance(p_dns, list) else [p_dns] if p_dns else []):
            entry = {"type": "dns", "address": dns}
            if entry not in services:
                services.append(entry)

    _add_link_and_network(port_id, mac, fixed_ip, cidr, gateway, dns_list, 0)

    # Extra ports (multi-NIC): metadata stores them as extra_ports list if present
    for idx, extra_port in enumerate(metadata.get("extra_ports") or [], start=1):
        _add_link_and_network(
            extra_port.get("id"),
            extra_port.get("mac_address"),
            extra_port.get("fixed_ip") or extra_port.get("ip_address"),
            extra_port.get("cidr"),
            extra_port.get("gateway_ip"),
            extra_port.get("dns_nameservers") or [],
            idx,
        )

    return {"links": links, "networks": networks, "services": services}


# ── vendor_data.json ───────────────────────────────────────────────────────────

def vendor_data_json(metadata: dict) -> dict:
    raw = metadata.get("vendor_data") or "{}"
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}


def vendor_data2_json(metadata: dict) -> dict:
    """vendor_data2.json — OpenStack-style structured vendor data (v2).

    Returns a dict with optional 'static' and 'dynamic' keys mirroring
    the real OpenStack vendor_data2 format.
    """
    base = vendor_data_json(metadata)
    return {
        "static": base,
        "dynamic": {
            "instance-identity": _instance_identity_doc(metadata),
        },
    }


# ── Instance Identity ──────────────────────────────────────────────────────────

def _instance_identity_doc(metadata: dict) -> dict:
    """OpenStack instance identity document (similar to AWS instance identity)."""
    return {
        "uuid": metadata.get("uuid", ""),
        "name": metadata.get("name", ""),
        "hostname": metadata.get("hostname") or metadata.get("name", ""),
        "project_id": metadata.get("project_id", ""),
        "instance_type": metadata.get("instance_type") or "unknown",
        "availability_zone": "default",
        "region": "cloudx-region-1",
        "created_at": metadata.get("created_at", ""),
        "issued_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def instance_identity_document(metadata: dict) -> dict:
    return _instance_identity_doc(metadata)


def instance_identity_signature(metadata: dict) -> str:
    """HMAC-SHA256 signature of the identity document.

    Only meaningful when METADATA_HMAC_KEY is set in the environment.
    Returns empty string when key is not configured (unsigned).
    """
    if not METADATA_HMAC_KEY:
        return ""
    doc = json.dumps(_instance_identity_doc(metadata), sort_keys=True).encode()
    sig = hmac.new(METADATA_HMAC_KEY, doc, hashlib.sha256).hexdigest()
    return sig
