import logging
import os

from shared import catalog

log = logging.getLogger("compute-api")
AUTH_API_URL = os.getenv("AUTH_API_URL", "http://auth-api:8000").rstrip("/")
COMPUTE_BACKEND = os.getenv("COMPUTE_BACKEND", "libvirt").lower()
DEFAULT_VM_USER = os.getenv("DEFAULT_VM_USER", "ubuntu")
NOVNC_BASE_URL = os.getenv("NOVNC_BASE_URL", "http://localhost:6080/vnc.html")
RPC_CALL_TIMEOUT = int(os.getenv("RPC_CALL_TIMEOUT", "30"))
ONLY_BACKEND = "libvirt"
# Compute nodes that may receive new servers, as named by COMPUTE_HOST on each compute-worker (comma separated).
# Empty = ask the placement API which nodes registered.
COMPUTE_HOSTS = [h.strip() for h in os.getenv("COMPUTE_HOSTS", "").split(",") if h.strip()]


def image_api_url() -> str:
    return catalog.url("image").rstrip("/")


def networking_api_url() -> str:
    return catalog.url("network").rstrip("/")


def block_store_api_url() -> str:
    return catalog.url("volume").rstrip("/")


def placement_api_url() -> str:
    return catalog.url("placement").rstrip("/")
