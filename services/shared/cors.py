import os


def cors_origins() -> list[str]:
    """Return allowed CORS origins from CORS_ORIGINS env var. Never returns wildcard '*' and defaults to ['http://localhost']."""
    raw = os.getenv("CORS_ORIGINS", "http://localhost")
    origins = [o.strip() for o in raw.split(",") if o.strip() and o.strip() != "*"]
    return origins or ["http://localhost"]
