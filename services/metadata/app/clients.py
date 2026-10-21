import requests
from fastapi import HTTPException

from shared.catalog import CatalogError
from shared.internal import internal_headers

from .config import compute_api_url


def compute_get(path: str, params: dict | None = None) -> dict:
    try:
        response = requests.get(f"{compute_api_url()}{path}", params=params, headers=internal_headers(), timeout=8)
    except (requests.RequestException, CatalogError) as exc:
        raise HTTPException(status_code=502, detail=f"compute-api request failed: {exc}") from exc
    if response.status_code == 404:
        raise HTTPException(status_code=404, detail="instance metadata not found")
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=response.text)
    return response.json()
