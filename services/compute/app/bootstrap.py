
from .db import SessionLocal
from .lookups import get_flavor
from .models import Flavor


def bootstrap_flavors():
    defaults = [
        {"name": "tiny", "vcpus": 1, "ram_mb": 512, "disk_gb": 5},
        {"name": "small", "vcpus": 1, "ram_mb": 1024, "disk_gb": 10},
        {"name": "medium", "vcpus": 2, "ram_mb": 2048, "disk_gb": 20},
    ]
    db = SessionLocal()
    try:
        for item in defaults:
            if not get_flavor(db, item["name"]):
                db.add(Flavor(**item, ephemeral_gb=0, swap_mb=0, is_public=True))
        db.commit()
    finally:
        db.close()
