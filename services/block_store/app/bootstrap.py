import os

from .config import log
from .db import SessionLocal
from .models import VolumeQuota


def _bootstrap_quotas() -> None:
    """Inject default quotas for admin project if not already present."""
    db = SessionLocal()
    try:
        # Try to find the admin project - use project_id "admin" as fallback
        ADMIN_PROJECT_ID = os.getenv("AUTH_ADMIN_PROJECT", "admin")
        existing = db.get(VolumeQuota, ADMIN_PROJECT_ID)
        if not existing:
            db.add(VolumeQuota(
                project_id=ADMIN_PROJECT_ID,
                volumes=50,
                gigabytes=10000,
                snapshots=50,
            ))
            db.commit()
            log.info("[block-store] bootstrap: default quota injected for project '%s'", ADMIN_PROJECT_ID)
    except Exception as exc:
        log.warning("[block-store] bootstrap_quotas failed: %s", exc)
    finally:
        db.close()

