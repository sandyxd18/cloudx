import os

COMPUTE_API_URL = os.getenv("COMPUTE_API_URL", "http://compute-api:8000").rstrip("/")
METADATA_HMAC_KEY = os.getenv("METADATA_HMAC_KEY", "").encode()  # set via env for HMAC signing
METADATA_BASE_URL = os.getenv("METADATA_BASE_URL", "http://169.254.169.254")
