from fastapi import APIRouter

router = APIRouter()


# ── API Version Discovery ────────────────────────────────────────────────────────

@router.get("/v2.1")
def nova_v21_root():
    """Compute API version discovery."""
    return {
        "version": {
            "id": "v2.1",
            "status": "CURRENT",
            "version": "2.95",
            "min_version": "2.1",
            "updated": "2024-01-01T00:00:00Z",
            "links": [{"rel": "self", "href": "/v2.1"}],
            "media-types": [
                {"base": "application/json", "type": "application/vnd.openstack.compute+json;version=2.1"}
            ],
        }
    }


@router.get("/")
def compute_root():
    """Compute API root — version discovery."""
    return {
        "versions": [{
            "id": "v2.1",
            "status": "CURRENT",
            "links": [{"rel": "self", "href": "/v2.1"}],
        }]
    }
