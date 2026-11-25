import logging
import os
from pathlib import Path

from shared import catalog

log = logging.getLogger("compute-worker")

LIBVIRT_URI = os.getenv("LIBVIRT_URI", "qemu:///system")
INSTANCE_DIR = Path(os.getenv("COMPUTE_INSTANCE_DIR", "/var/lib/cloudx/compute/instances"))
IMAGE_CACHE_DIR = Path(os.getenv("COMPUTE_IMAGE_CACHE_DIR", "/var/lib/cloudx/compute/cache/images"))
OVS_BRIDGE = os.getenv("OVS_BRIDGE", "br-int")
VM_NAME_PREFIX = os.getenv("VM_NAME_PREFIX", "mini-os")
DEFAULT_VM_USER = os.getenv("DEFAULT_VM_USER", "ubuntu")
RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://cloudx:cloudx@127.0.0.1:5672/")
# How long a build waits for its root volume (importing a large image into Ceph can take many minutes) before the server goes to ERROR
ROOT_VOLUME_WAIT_TIMEOUT = int(os.getenv("ROOT_VOLUME_WAIT_TIMEOUT", "3600"))


# env name -> (catalog name, last-resort address when neither the catalog nor the message knows it)
_PEERS = {
    "IMAGE_API_URL": ("image", "http://image-api:8000"),
    "BLOCK_STORE_API_URL": ("volume", "http://block-store-api:8000"),
    "NETWORKING_API_URL": ("network", "http://localhost:8001"),
}


def endpoint(args: dict, key: str, env_name: str, default: str | None = None) -> str:
    """Address of another service: the worker's own <env_name> setting, else the service catalog, else the address
    in the message (args[key]), else the default. The message comes last because compute-api sends the address as
    *it* reaches the service (often 127.0.0.1), which is wrong on every other node. The catalog answers the worker's
    own override first, so a non-empty <env_name> in the environment always wins."""
    service, fallback = _PEERS[env_name]
    try:
        return catalog.url(service).rstrip("/")
    except catalog.CatalogError as exc:
        log.info("%s: %s; using the address in the message or the default", env_name, exc)
    return (args.get(key) or default or fallback).rstrip("/")


# Name of this compute node: its placement provider, server.host, and the suffix of its own RPC queue.
COMPUTE_HOST = os.getenv("COMPUTE_HOST", os.getenv("NODE_NAME", "cloudx-compute-01"))
# Address browsers use to reach this node's websockify on port 6080 (the console link compute-api hands out); defaults to the node name.
CONSOLE_PROXY_HOST = os.getenv("CONSOLE_PROXY_HOST") or COMPUTE_HOST

# Live migration. The host is the address other nodes use to reach this one; the URI is a template for the libvirt
# connection to the destination ({host} is replaced by the destination's live_migration_host).
LIVE_MIGRATION_HOST = os.getenv("LIVE_MIGRATION_HOST") or COMPUTE_HOST
LIVE_MIGRATION_URI = os.getenv("LIVE_MIGRATION_URI", "qemu+ssh://root@{host}/system")
LIVE_MIGRATION_TIMEOUT = int(os.getenv("LIVE_MIGRATION_TIMEOUT", "1800"))
LIVE_MIGRATION_MAX_DOWNTIME_MS = int(os.getenv("LIVE_MIGRATION_MAX_DOWNTIME_MS", "300"))
LIVE_MIGRATION_BANDWIDTH_MBPS = int(os.getenv("LIVE_MIGRATION_BANDWIDTH_MBPS", "0"))  # megabits per second, 0 = unlimited
LIVE_MIGRATION_AUTO_CONVERGE = os.getenv("LIVE_MIGRATION_AUTO_CONVERGE", "false").strip().lower() in ("true", "1", "yes")
