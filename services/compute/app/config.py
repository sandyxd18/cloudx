import logging
import os

log = logging.getLogger("compute-api")
IMAGE_API_URL = os.getenv("IMAGE_API_URL", "http://image-api:8000").rstrip("/")
NETWORKING_API_URL = os.getenv("NETWORKING_API_URL", "http://networking-api:8000").rstrip("/")
BLOCK_STORE_API_URL = os.getenv("BLOCK_STORE_API_URL", "http://block-store-api:8000").rstrip("/")
AUTH_API_URL = os.getenv("AUTH_API_URL", "http://auth-api:8000").rstrip("/")
PLACEMENT_API_URL = os.getenv("PLACEMENT_API_URL", "http://placement-api:8000").rstrip("/")
AUTH_ADMIN_USERNAME = os.getenv("AUTH_ADMIN_USERNAME", "admin")
AUTH_ADMIN_PASSWORD = os.environ["AUTH_ADMIN_PASSWORD"]
COMPUTE_BACKEND = os.getenv("COMPUTE_BACKEND", "libvirt").lower()
DEFAULT_VM_USER = os.getenv("DEFAULT_VM_USER", "ubuntu")
NOVNC_BASE_URL = os.getenv("NOVNC_BASE_URL", "http://localhost:6080/vnc.html")
RPC_CALL_TIMEOUT = int(os.getenv("RPC_CALL_TIMEOUT", "30"))
ONLY_BACKEND = "libvirt"
