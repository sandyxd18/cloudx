from fastapi import APIRouter

router = APIRouter()


# ── API Version Discovery ────────────────────────────────────────────────────────

@router.get("/v3")
def cinder_v3_root():
    """Block Storage API version discovery."""
    return {
        "version": {
            "id": "v3.0",
            "status": "CURRENT",
            "version": "3.70",
            "min_version": "3.0",
            "updated": "2024-01-01T00:00:00Z",
            "links": [{"rel": "self", "href": "/v3"}],
        }
    }


@router.get("/")
def block_store_root():
    """Block storage API root — version discovery."""
    return {
        "versions": [{
            "id": "v3.0",
            "status": "CURRENT",
            "links": [{"rel": "self", "href": "/v3"}],
        }]
    }
