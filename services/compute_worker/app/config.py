import os
from pathlib import Path


LIBVIRT_URI = os.getenv("LIBVIRT_URI", "qemu:///system")
IMAGE_API_URL = os.getenv("IMAGE_API_URL", "http://image-api:8000").rstrip("/")
BLOCK_STORE_API_URL = os.getenv("BLOCK_STORE_API_URL", "http://block-store-api:8000").rstrip("/")
INSTANCE_DIR = Path(os.getenv("COMPUTE_INSTANCE_DIR", "/var/lib/cloudx/compute/instances"))
IMAGE_CACHE_DIR = Path(os.getenv("COMPUTE_IMAGE_CACHE_DIR", "/var/lib/cloudx/compute/cache/images"))
OVS_BRIDGE = os.getenv("OVS_BRIDGE", "br-int")
VM_NAME_PREFIX = os.getenv("VM_NAME_PREFIX", "mini-os")
DEFAULT_VM_USER = os.getenv("DEFAULT_VM_USER", "ubuntu")
RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://cloudx:cloudx@127.0.0.1:5672/")
# Name of this compute node: its placement provider, server.host, and the suffix of its own RPC queue.
COMPUTE_HOST = os.getenv("COMPUTE_HOST", os.getenv("NODE_NAME", "cloudx-compute-01"))
