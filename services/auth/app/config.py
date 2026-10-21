import logging
import os

from shared.services import SERVICES

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
    {"name": s.name, "type": s.type, "url": os.getenv(s.url_key.upper(), f"http://127.0.0.1:{s.port}")}
    for s in SERVICES
]
