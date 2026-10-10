"""
cloudx metadata-api — EC2 + OpenStack compatible instance metadata service.

Serves:
  - EC2-style:       /latest/meta-data/*, /latest/user-data
  - OpenStack-style: /openstack/latest/meta_data.json, network_data.json,
                     vendor_data.json, vendor_data2.json, password
  - Instance identity: /openstack/latest/identity (JSON + HMAC signature)
  - Versioned EC2:   /{version}/meta-data/*, /{version}/user-data
"""
from shared.config import load_service_config
load_service_config("metadata-api")

import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse

from .metadata_service import (
    default_user_data,
    first_public_key,
    instance_identity_document,
    instance_identity_signature,
    latest_index,
    lookup_metadata,
    meta_data_index,
    meta_data_json,
    network_data_json,
    openstack_index,
    openstack_latest_index,
    public_key_zero_index,
    public_keys_index,
    vendor_data2_json,
    vendor_data_json,
    versions,
)


app = FastAPI(title="cloudx metadata-api", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost").split(",") if o.strip() and o.strip() != "*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok", "service": "metadata-api"}


# ── Root index ─────────────────────────────────────────────────────────────────

@app.get("/", response_class=PlainTextResponse)
def root():
    return versions()


@app.get("/latest/", response_class=PlainTextResponse)
def latest():
    return latest_index()


@app.get("/openstack/", response_class=PlainTextResponse)
def openstack():
    return openstack_index()


@app.get("/openstack/latest/", response_class=PlainTextResponse)
def openstack_latest():
    return openstack_latest_index()


@app.get("/latest/meta-data/", response_class=PlainTextResponse)
def latest_meta_data():
    return meta_data_index()


# ── EC2-style latest/ endpoints ────────────────────────────────────────────────

@app.get("/latest/meta-data/instance-id", response_class=PlainTextResponse)
def latest_instance_id(request: Request, instance_id: str | None = None, port_id: str | None = None, fixed_ip: str | None = None):
    return lookup_metadata(request, instance_id, port_id, fixed_ip)["uuid"] + "\n"


@app.get("/latest/meta-data/local-hostname", response_class=PlainTextResponse)
def latest_local_hostname(request: Request, instance_id: str | None = None, port_id: str | None = None, fixed_ip: str | None = None):
    return lookup_metadata(request, instance_id, port_id, fixed_ip)["local_hostname"] + "\n"


@app.get("/latest/meta-data/hostname", response_class=PlainTextResponse)
def latest_hostname(request: Request, instance_id: str | None = None, port_id: str | None = None, fixed_ip: str | None = None):
    return lookup_metadata(request, instance_id, port_id, fixed_ip)["hostname"] + "\n"


@app.get("/latest/meta-data/instance-type", response_class=PlainTextResponse)
def latest_instance_type(request: Request, instance_id: str | None = None, port_id: str | None = None, fixed_ip: str | None = None):
    metadata = lookup_metadata(request, instance_id, port_id, fixed_ip)
    return (metadata.get("instance_type") or "unknown") + "\n"


@app.get("/latest/meta-data/local-ipv4", response_class=PlainTextResponse)
def latest_local_ipv4(request: Request, instance_id: str | None = None, port_id: str | None = None, fixed_ip: str | None = None):
    metadata = lookup_metadata(request, instance_id, port_id, fixed_ip)
    port = metadata.get("network") or {}
    ip = metadata.get("fixed_ip") or port.get("fixed_ip") or port.get("ip_address") or ""
    if not ip:
        raise HTTPException(status_code=404, detail="no local IPv4 assigned")
    return ip + "\n"


@app.get("/latest/meta-data/public-keys/", response_class=PlainTextResponse)
def latest_public_keys(request: Request, instance_id: str | None = None, port_id: str | None = None, fixed_ip: str | None = None):
    return public_keys_index(lookup_metadata(request, instance_id, port_id, fixed_ip))


@app.get("/latest/meta-data/public-keys/0/", response_class=PlainTextResponse)
def latest_public_key_zero(request: Request, instance_id: str | None = None, port_id: str | None = None, fixed_ip: str | None = None):
    return public_key_zero_index(lookup_metadata(request, instance_id, port_id, fixed_ip))


@app.get("/latest/meta-data/public-keys/0/openssh-key", response_class=PlainTextResponse)
def latest_openssh_key(request: Request, instance_id: str | None = None, port_id: str | None = None, fixed_ip: str | None = None):
    return first_public_key(lookup_metadata(request, instance_id, port_id, fixed_ip))


@app.get("/latest/user-data", response_class=PlainTextResponse)
def latest_user_data(request: Request, instance_id: str | None = None, port_id: str | None = None, fixed_ip: str | None = None):
    return default_user_data(lookup_metadata(request, instance_id, port_id, fixed_ip))


# ── OpenStack latest/ endpoints ────────────────────────────────────────────────

@app.get("/openstack/latest/meta_data.json")
def openstack_meta_data(request: Request, instance_id: str | None = None, port_id: str | None = None, fixed_ip: str | None = None):
    return JSONResponse(meta_data_json(lookup_metadata(request, instance_id, port_id, fixed_ip)))


@app.get("/openstack/latest/network_data.json")
def openstack_network_data(request: Request, instance_id: str | None = None, port_id: str | None = None, fixed_ip: str | None = None):
    return JSONResponse(network_data_json(lookup_metadata(request, instance_id, port_id, fixed_ip)))


@app.get("/openstack/latest/vendor_data.json")
def openstack_vendor_data(request: Request, instance_id: str | None = None, port_id: str | None = None, fixed_ip: str | None = None):
    return JSONResponse(vendor_data_json(lookup_metadata(request, instance_id, port_id, fixed_ip)))


@app.get("/openstack/latest/vendor_data2.json")
def openstack_vendor_data2(request: Request, instance_id: str | None = None, port_id: str | None = None, fixed_ip: str | None = None):
    """OpenStack vendor_data2 format with static + dynamic.instance-identity."""
    return JSONResponse(vendor_data2_json(lookup_metadata(request, instance_id, port_id, fixed_ip)))


@app.get("/openstack/latest/user_data", response_class=PlainTextResponse)
def openstack_user_data(request: Request, instance_id: str | None = None, port_id: str | None = None, fixed_ip: str | None = None):
    return default_user_data(lookup_metadata(request, instance_id, port_id, fixed_ip))


@app.get("/openstack/latest/password", response_class=PlainTextResponse)
def openstack_password(request: Request, instance_id: str | None = None, port_id: str | None = None, fixed_ip: str | None = None):
    """Placeholder: password endpoint (real implementation needs agent)."""
    return ""


# ── Instance identity (OpenStack extension) ────────────────────────────────────

@app.get("/openstack/latest/identity")
def openstack_identity(request: Request, instance_id: str | None = None, port_id: str | None = None, fixed_ip: str | None = None):
    """Instance identity document + optional HMAC-SHA256 signature."""
    metadata = lookup_metadata(request, instance_id, port_id, fixed_ip)
    doc = instance_identity_document(metadata)
    sig = instance_identity_signature(metadata)
    return JSONResponse({"document": doc, "signature": sig, "pkcs7": ""})


@app.get("/openstack/latest/identity/document")
def openstack_identity_document(request: Request, instance_id: str | None = None, port_id: str | None = None, fixed_ip: str | None = None):
    metadata = lookup_metadata(request, instance_id, port_id, fixed_ip)
    return JSONResponse(instance_identity_document(metadata))


@app.get("/openstack/latest/identity/signature", response_class=PlainTextResponse)
def openstack_identity_signature(request: Request, instance_id: str | None = None, port_id: str | None = None, fixed_ip: str | None = None):
    metadata = lookup_metadata(request, instance_id, port_id, fixed_ip)
    return instance_identity_signature(metadata)


# ── EC2 dated versions ─────────────────────────────────────────────────────────
# Keep these AFTER the explicit latest/ and openstack/ routes.

@app.get("/{version}/", response_class=PlainTextResponse)
def ec2_index(version: str):
    return latest_index()


@app.get("/{version}/meta-data/", response_class=PlainTextResponse)
def ec2_meta_data(version: str):
    return meta_data_index()


@app.get("/{version}/meta-data/instance-id", response_class=PlainTextResponse)
def ec2_instance_id(version: str, request: Request, instance_id: str | None = None, port_id: str | None = None, fixed_ip: str | None = None):
    return lookup_metadata(request, instance_id, port_id, fixed_ip)["uuid"] + "\n"


@app.get("/{version}/instance-id", response_class=PlainTextResponse)
def ec2_instance_id_shortcut(version: str, request: Request, instance_id: str | None = None, port_id: str | None = None, fixed_ip: str | None = None):
    return lookup_metadata(request, instance_id, port_id, fixed_ip)["uuid"] + "\n"


@app.get("/{version}/meta-data/local-hostname", response_class=PlainTextResponse)
def ec2_local_hostname(version: str, request: Request, instance_id: str | None = None, port_id: str | None = None, fixed_ip: str | None = None):
    return lookup_metadata(request, instance_id, port_id, fixed_ip)["local_hostname"] + "\n"


@app.get("/{version}/meta-data/hostname", response_class=PlainTextResponse)
def ec2_hostname(version: str, request: Request, instance_id: str | None = None, port_id: str | None = None, fixed_ip: str | None = None):
    return lookup_metadata(request, instance_id, port_id, fixed_ip)["hostname"] + "\n"


@app.get("/{version}/meta-data/instance-type", response_class=PlainTextResponse)
def ec2_instance_type(version: str, request: Request, instance_id: str | None = None, port_id: str | None = None, fixed_ip: str | None = None):
    metadata = lookup_metadata(request, instance_id, port_id, fixed_ip)
    return (metadata.get("instance_type") or "unknown") + "\n"


@app.get("/{version}/meta-data/local-ipv4", response_class=PlainTextResponse)
def ec2_local_ipv4(version: str, request: Request, instance_id: str | None = None, port_id: str | None = None, fixed_ip: str | None = None):
    metadata = lookup_metadata(request, instance_id, port_id, fixed_ip)
    port = metadata.get("network") or {}
    ip = metadata.get("fixed_ip") or port.get("fixed_ip") or port.get("ip_address") or ""
    if not ip:
        raise HTTPException(status_code=404, detail="no local IPv4 assigned")
    return ip + "\n"


@app.get("/{version}/meta-data/public-ipv4", response_class=PlainTextResponse)
def ec2_public_ipv4(version: str):
    raise HTTPException(status_code=404, detail="no public IPv4 (no floating IP associated)")


@app.get("/{version}/meta-data/public-hostname", response_class=PlainTextResponse)
def ec2_public_hostname(version: str):
    raise HTTPException(status_code=404, detail="no public hostname")


@app.get("/{version}/meta-data/ami-launch-index", response_class=PlainTextResponse)
def ec2_ami_launch_index(version: str):
    return "0\n"


@app.get("/{version}/meta-data/block-device-mapping", response_class=PlainTextResponse)
@app.get("/{version}/meta-data/block-device-mapping/", response_class=PlainTextResponse)
def ec2_block_device_mapping(version: str):
    raise HTTPException(status_code=404, detail="block-device-mapping not supported")


@app.get("/{version}/meta-data/public-keys/", response_class=PlainTextResponse)
@app.get("/{version}/public-keys/", response_class=PlainTextResponse)
def ec2_public_keys(version: str, request: Request, instance_id: str | None = None, port_id: str | None = None, fixed_ip: str | None = None):
    return public_keys_index(lookup_metadata(request, instance_id, port_id, fixed_ip))


@app.get("/{version}/meta-data/public-keys/0/", response_class=PlainTextResponse)
@app.get("/{version}/public-keys/0/", response_class=PlainTextResponse)
def ec2_public_key_zero(version: str, request: Request, instance_id: str | None = None, port_id: str | None = None, fixed_ip: str | None = None):
    return public_key_zero_index(lookup_metadata(request, instance_id, port_id, fixed_ip))


@app.get("/{version}/meta-data/public-keys/0/openssh-key", response_class=PlainTextResponse)
@app.get("/{version}/public-keys/0/openssh-key", response_class=PlainTextResponse)
def ec2_openssh_key(version: str, request: Request, instance_id: str | None = None, port_id: str | None = None, fixed_ip: str | None = None):
    return first_public_key(lookup_metadata(request, instance_id, port_id, fixed_ip))


@app.get("/{version}/user-data", response_class=PlainTextResponse)
def ec2_user_data(version: str, request: Request, instance_id: str | None = None, port_id: str | None = None, fixed_ip: str | None = None):
    return default_user_data(lookup_metadata(request, instance_id, port_id, fixed_ip))
