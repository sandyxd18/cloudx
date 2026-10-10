from __future__ import annotations

import os


def get_db_url(default_db_name: str = "cloudx", env_var: str = "DATABASE_URL") -> str:
    """
    Get database URL from environment variable (`DATABASE_URL` or `env_var`).
    If unset or empty, fails with RuntimeError unless CLOUDX_ALLOW_DEFAULT_DB=1 is set,
    in which case it returns postgresql+psycopg://cloudx:cloudx@127.0.0.1:5432/{default_db_name}.
    """
    url = os.getenv(env_var) or (os.getenv("DATABASE_URL") if env_var != "DATABASE_URL" else None)
    if url:
        return url
    if os.getenv("CLOUDX_ALLOW_DEFAULT_DB") == "1":
        return f"postgresql+psycopg://cloudx:cloudx@127.0.0.1:5432/{default_db_name}"
    raise RuntimeError(
        f"Database configuration error: {env_var} and DATABASE_URL are unset. "
        "Set DATABASE_URL in environment/cloudx.conf, or set CLOUDX_ALLOW_DEFAULT_DB=1 to allow default credentials."
    )
