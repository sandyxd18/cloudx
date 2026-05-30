from fastapi import HTTPException, Request

from .clients import compute_get


def lookup_metadata(request: Request, instance_id: str | None = None) -> dict:
    params: dict[str, str] = {}
    header_instance_id = request.headers.get("X-Instance-ID")
    forwarded_for = request.headers.get("X-Forwarded-For")
    port_id = request.headers.get("X-Neutron-Port-ID")
    if instance_id or header_instance_id:
        params["instance_id"] = instance_id or header_instance_id
    elif port_id:
        params["port_id"] = port_id
    elif forwarded_for:
        params["fixed_ip"] = forwarded_for.split(",")[0].strip()
    else:
        raise HTTPException(status_code=400, detail="metadata lookup needs instance_id, X-Instance-ID, X-Forwarded-For, or X-Neutron-Port-ID")
    return compute_get("/servers/metadata-lookup", params=params)


def versions() -> str:
    return "latest\nopenstack\n"


def latest_index() -> str:
    return "meta-data/\nuser-data\n"


def meta_data_index() -> str:
    return "instance-id\nlocal-hostname\npublic-keys/\n"


def public_keys_index(metadata: dict) -> str:
    keys = list((metadata.get("public_keys") or {}).keys())
    if not keys:
        return ""
    return "\n".join(f"{idx}={name}" for idx, name in enumerate(keys)) + "\n"


def first_public_key(metadata: dict) -> str:
    keys = metadata.get("public_keys") or {}
    if not keys:
        raise HTTPException(status_code=404, detail="no public key for instance")
    return next(iter(keys.values())) + "\n"


def default_user_data(metadata: dict) -> str:
    return metadata.get("user_data") or "#cloud-config\npackage_update: false\n"
