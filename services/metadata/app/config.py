import os


COMPUTE_API_URL = os.getenv("COMPUTE_API_URL", "http://compute-api:8000").rstrip("/")
