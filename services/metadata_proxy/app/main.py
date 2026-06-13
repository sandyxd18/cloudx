import os

import requests
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

METADATA_API_URL = os.getenv("METADATA_API_URL", "http://metadata-api:8775").rstrip("/")
NETWORKING_API_URL = os.getenv("NETWORKING_API_URL", "http://networking-api:8000").rstrip("/")

app = FastAPI(title="mini OpenStack lab metadata-proxy", version="0.1.0")


@app.get("/health")
def health():
    return {"status": "ok", "service": "metadata-proxy"}


def source_ip(request: Request) -> str | None:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def lookup_port(ip: str | None) -> dict:
    if not ip:
        return {}
    try:
        response = requests.get(f"{NETWORKING_API_URL}/ports", timeout=5)
        response.raise_for_status()
        for port in response.json().get("items", []):
            if ip in {port.get("fixed_ip"), port.get("ip_address")}:
                return port
    except requests.RequestException:
        return {}
    return {}


def forward(path: str, request: Request) -> Response:
    ip = source_ip(request)
    port = lookup_port(ip)
    headers = {"X-Forwarded-For": ip or ""}
    if port.get("id"):
        headers["X-Neutron-Port-ID"] = port["id"]
    if port.get("device_id"):
        headers["X-Instance-ID"] = port["device_id"]
    try:
        upstream = requests.get(f"{METADATA_API_URL}/{path}", params=dict(request.query_params), headers=headers, timeout=10)
    except requests.RequestException as exc:
        return JSONResponse({"detail": f"metadata proxy upstream failed: {exc}"}, status_code=502)
    return Response(content=upstream.content, status_code=upstream.status_code, media_type=upstream.headers.get("content-type"))


@app.api_route("/{path:path}", methods=["GET"])
def proxy(path: str, request: Request):
    # Allow: empty root, 'latest/', 'openstack/', and EC2-style versioned paths
    # like '2009-04-04/...', '2010-08-11/...', etc. used by CirrOS and older cloud-init.
    is_ec2_versioned = len(path) >= 10 and path[4] == "-" and path[7] == "-" and path[:4].isdigit()
    if not (path == "" or path.startswith("latest") or path.startswith("openstack") or is_ec2_versioned):
        return JSONResponse({"detail": "metadata path not found"}, status_code=404)
    return forward(path, request)
