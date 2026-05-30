from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from .metadata_service import (
    default_user_data,
    first_public_key,
    latest_index,
    lookup_metadata,
    meta_data_index,
    public_keys_index,
    versions,
)


app = FastAPI(title="mini OpenStack lab metadata-api", version="0.1.0")


@app.get("/health")
def health():
    return {"status": "ok", "service": "metadata-api"}


@app.get("/", response_class=PlainTextResponse)
def root():
    return versions()


@app.get("/latest/", response_class=PlainTextResponse)
def latest():
    return latest_index()


@app.get("/latest/meta-data/", response_class=PlainTextResponse)
def latest_meta_data():
    return meta_data_index()


@app.get("/latest/meta-data/instance-id", response_class=PlainTextResponse)
def instance_id(request: Request, instance_id: str | None = None):
    return lookup_metadata(request, instance_id)["uuid"] + "\n"


@app.get("/latest/meta-data/local-hostname", response_class=PlainTextResponse)
def local_hostname(request: Request, instance_id: str | None = None):
    return lookup_metadata(request, instance_id)["local_hostname"] + "\n"


@app.get("/latest/meta-data/public-keys/", response_class=PlainTextResponse)
def public_keys(request: Request, instance_id: str | None = None):
    return public_keys_index(lookup_metadata(request, instance_id))


@app.get("/latest/meta-data/public-keys/0/openssh-key", response_class=PlainTextResponse)
def openssh_key(request: Request, instance_id: str | None = None):
    return first_public_key(lookup_metadata(request, instance_id))


@app.get("/latest/user-data", response_class=PlainTextResponse)
def user_data(request: Request, instance_id: str | None = None):
    return default_user_data(lookup_metadata(request, instance_id))


@app.get("/openstack/latest/meta_data.json")
def openstack_meta_data(request: Request, instance_id: str | None = None):
    metadata = lookup_metadata(request, instance_id)
    return JSONResponse(
        {
            "uuid": metadata["uuid"],
            "hostname": metadata["hostname"],
            "name": metadata["name"],
            "public_keys": metadata.get("public_keys") or {},
            "meta": metadata.get("meta") or {},
            "devices": metadata.get("devices") or [],
        }
    )


@app.get("/openstack/latest/user_data", response_class=PlainTextResponse)
def openstack_user_data(request: Request, instance_id: str | None = None):
    return default_user_data(lookup_metadata(request, instance_id))
