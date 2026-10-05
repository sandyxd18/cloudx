import logging
import os

log = logging.getLogger("block-store-api")
AUTH_API_URL = os.getenv("AUTH_API_URL", "http://auth-api:8000").rstrip("/")
COMPUTE_API_URL = os.getenv("COMPUTE_API_URL", "http://compute-api:8000").rstrip("/")
IMAGE_API_URL   = os.getenv("IMAGE_API_URL", "http://image-api:8000").rstrip("/")
RABBITMQ_URL    = os.getenv("RABBITMQ_URL", "amqp://cloudx:cloudx@127.0.0.1:5672/")
RPC_CALL_TIMEOUT = int(os.getenv("RPC_CALL_TIMEOUT", "30"))
# These are kept only for the health endpoint — the API itself doesn't use drivers
STORAGE_LVM_DRIVER      = os.getenv("STORAGE_LVM_DRIVER", "false").lower() == "true"
STORAGE_CEPH_RBD_DRIVER = os.getenv("STORAGE_CEPH_RBD_DRIVER", "false").lower() == "true"
STORAGE_NFS_DRIVER      = os.getenv("STORAGE_NFS_DRIVER", "true").lower() == "true"
LVM_VG_NAME  = os.getenv("LVM_VG_NAME", "")
CEPH_POOL    = os.getenv("CEPH_POOL", "")
