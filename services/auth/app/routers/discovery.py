from fastapi import APIRouter

router = APIRouter()


# ── API Version Discovery ──────────────────────────────────────────────────────

@router.get("/v3")
def keystone_v3_root():
    """Identity API version discovery."""
    return {
        "version": {
            "id": "v3",
            "status": "stable",
            "updated": "2024-01-01T00:00:00Z",
            "links": [{"rel": "self", "href": "/v3"}],
            "media-types": [
                {"base": "application/json", "type": "application/vnd.openstack.identity-v3+json"}
            ],
        }
    }


@router.get("/")
def auth_root():
    """Identity API root — version discovery."""
    return {
        "versions": {
            "values": [{
                "id": "v3",
                "status": "stable",
                "links": [{"rel": "self", "href": "/v3"}],
            }]
        }
    }
