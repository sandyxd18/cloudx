from fastapi import APIRouter

router = APIRouter()

# ── API Version Discovery ────────────────────────────────────────────────────────

@router.get("/v2.0")
def neutron_v2_root():
    """Networking API version discovery."""
    return {
        "version": {
            "id": "v2.0",
            "status": "CURRENT",
            "updated": "2013-07-23T11:33:21Z",
            "links": [{"rel": "self", "href": "/v2.0"}],
        }
    }

@router.get("/")
def networking_root():
    """Networking API root — version discovery."""
    return {
        "versions": [{
            "id": "v2.0",
            "status": "CURRENT",
            "links": [{"rel": "self", "href": "/v2.0"}],
        }]
    }
