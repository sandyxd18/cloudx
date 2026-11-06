"""References sent to a peer service as one URL path segment."""
from urllib.parse import quote

from fastapi import HTTPException


def path_ref(ref: str) -> str:
    """`ref` quoted for a URL path segment ('/' and '%' included).

    "." and ".." are refused with 404: a request normalises them into a parent path, so they never name a resource.
    """
    if ref in (".", ".."):
        raise HTTPException(status_code=404, detail=f"'{ref}' not found")
    return quote(ref, safe="")
