import os
from pathlib import Path


LIBVIRT_URI = os.getenv("LIBVIRT_URI", "qemu:///system")
IMAGE_API_URL = os.getenv("IMAGE_API_URL", "http://image-api:8000").rstrip("/")
BLOCK_STORE_API_URL = os.getenv("BLOCK_STORE_API_URL", "http://block-store-api:8000").rstrip("/")
INSTANCE_DIR = Path(os.getenv("COMPUTE_INSTANCE_DIR", "/var/lib/mini-openstack/compute/instances"))
IMAGE_CACHE_DIR = Path(os.getenv("COMPUTE_IMAGE_CACHE_DIR", "/var/lib/mini-openstack/compute/cache/images"))
OVS_BRIDGE = os.getenv("OVS_BRIDGE", "br-int")
VM_NAME_PREFIX = os.getenv("VM_NAME_PREFIX", "mini-os")
DEFAULT_VM_USER = os.getenv("DEFAULT_VM_USER", "ubuntu")
RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://cloudx:cloudx@rabbitmq:5672/")
