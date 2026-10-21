import os

from shared import catalog

METADATA_HMAC_KEY = os.getenv("METADATA_HMAC_KEY", "").encode()  # set via env for HMAC signing
METADATA_BASE_URL = os.getenv("METADATA_BASE_URL", "http://169.254.169.254")


def compute_api_url() -> str:
    return catalog.url("compute").rstrip("/")
