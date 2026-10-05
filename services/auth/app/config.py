import logging
import os

log = logging.getLogger("auth-api")

SECRET_KEY     = os.environ["AUTH_SECRET_KEY"]
ALGORITHM      = "HS256"
TOKEN_EXPIRE_H = int(os.getenv("AUTH_TOKEN_EXPIRE_HOURS", "24"))
ADMIN_USERNAME = os.getenv("AUTH_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ["AUTH_ADMIN_PASSWORD"]
ADMIN_EMAIL    = os.getenv("AUTH_ADMIN_EMAIL",    "admin@cloudx.local")
ADMIN_PROJECT  = os.getenv("AUTH_ADMIN_PROJECT",  "admin")
# Service URLs for service catalog bootstrap
_CLOUDX_SERVICES = [
    {"name": "identity",     "type": "identity",     "url": os.getenv("AUTH_API_URL",        "http://auth-api:8000")},
    {"name": "compute",      "type": "compute",      "url": os.getenv("COMPUTE_API_URL",    "http://compute-api:8000")},
    {"name": "image",        "type": "image",        "url": os.getenv("IMAGE_API_URL",      "http://image-api:8000")},
    {"name": "network",      "type": "network",      "url": os.getenv("NETWORKING_API_URL", "http://networking-api:8000")},
    {"name": "volume",       "type": "block-storage","url": os.getenv("BLOCK_STORE_API_URL","http://block-store-api:8000")},
    {"name": "placement",    "type": "placement",    "url": os.getenv("PLACEMENT_API_URL",  "http://placement-api:8000")},
    {"name": "object-store", "type": "object-store", "url": os.getenv("OBJECT_STORE_API_URL","http://object-store-api:8000")},
    {"name": "load-balancer","type": "load-balancer","url": os.getenv("LOAD_BALANCER_API_URL","http://load-balancer-api:8000")},
    {"name": "metadata",     "type": "metadata",     "url": os.getenv("METADATA_API_URL",   "http://metadata-api:8775")},
    {"name": "telemetry",    "type": "telemetry",    "url": os.getenv("TELEMETRY_API_URL",  "http://telemetry-api:8010")},
    {"name": "billing",      "type": "billing",      "url": os.getenv("BILLING_API_URL",    "http://billing-api:8011")},
    {"name": "event",        "type": "event",        "url": os.getenv("EVENT_API_URL",      "http://event-api:8012")},
    {"name": "orchestration","type": "orchestration","url": os.getenv("ORCHESTRATION_API_URL","http://orchestration-api:8013")},
]
