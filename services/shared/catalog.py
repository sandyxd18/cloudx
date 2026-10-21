"""Client side of the service catalog: resolves a service name to its URL.

Order: the `<url_key>` override in the environment (the conf loader injects conf keys there),
then the catalog served by auth (`GET /internal/catalog`), then CatalogError.
"""
import logging
import os
import threading
import time

import requests

from shared import services
from shared.internal import internal_headers

log = logging.getLogger("cloudx.catalog")

DEFAULT_TTL = 60.0
FETCH_TIMEOUT = 3
REQUEST_TIMEOUT = 10

_lock = threading.Lock()
_cache: tuple[float, list] | None = None


class CatalogError(RuntimeError):
    pass


def _ttl() -> float:
    try:
        ttl = float(os.environ.get("CATALOG_CACHE_TTL", ""))
    except ValueError:
        return DEFAULT_TTL
    return ttl if ttl >= 0 else DEFAULT_TTL


def _override(entry: services.CatalogService) -> str | None:
    value = os.environ.get(entry.url_key.upper(), "")
    return value if value.strip() else None


def _text(value) -> bool:
    return isinstance(value, str) and bool(value)


def _well_formed(entries) -> bool:
    return isinstance(entries, list) and all(
        isinstance(item, dict)
        and _text(item.get("name"))
        and isinstance(item.get("endpoints"), list)
        and all(
            isinstance(ep, dict) and _text(ep.get("interface")) and _text(ep.get("url"))
            for ep in item["endpoints"]
        )
        for item in entries
    )


def _fetch() -> list:
    auth_url = _override(services.by_name("identity"))
    if auth_url is None:
        raise CatalogError("auth_api_url is not set, so the catalog cannot be fetched")
    try:
        resp = requests.get(
            f"{auth_url.rstrip('/')}/internal/catalog", headers=internal_headers(), timeout=FETCH_TIMEOUT
        )
        if resp.status_code != 200:
            raise CatalogError(f"auth answered HTTP {resp.status_code}")
        entries = resp.json().get("catalog")
    except (requests.RequestException, ValueError, AttributeError) as exc:
        raise CatalogError(f"cannot fetch the catalog from {auth_url}: {exc}") from exc
    if not _well_formed(entries):
        raise CatalogError("auth returned a malformed catalog")
    return entries


def _catalog(service: str) -> list:
    global _cache
    with _lock:
        cached = _cache
    if cached is not None and time.monotonic() - cached[0] < _ttl():
        return cached[1]
    try:
        entries = _fetch()
    except CatalogError as exc:
        if cached is None:
            raise CatalogError(f"cannot resolve service '{service}': {exc}") from exc
        log.warning("catalog fetch failed (%s); serving the stale catalog", exc)
        return cached[1]
    with _lock:
        _cache = (time.monotonic(), entries)
    return entries


def _resolve(service: str, interface: str) -> tuple[str, bool]:
    """Returns (url, came_from_override)."""
    try:
        entry = services.by_name(service)
    except KeyError:
        raise CatalogError(f"unknown service '{service}'") from None
    override = _override(entry)
    if override is not None:
        return override, True
    if service == "identity":
        raise CatalogError("auth_api_url is not set (identity is never resolved through the catalog)")
    for item in _catalog(service):
        if item["name"] == service:
            for endpoint in item["endpoints"]:
                if endpoint["interface"] == interface:
                    return endpoint["url"], False
            raise CatalogError(f"service '{service}' has no '{interface}' endpoint in the catalog")
    raise CatalogError(f"service '{service}' is not in the catalog ({entry.url_key} is not set)")


def url(service: str, interface: str = "internal") -> str:
    return _resolve(service, interface)[0]


def invalidate() -> None:
    global _cache
    with _lock:
        _cache = None


def _expire() -> None:
    """Makes the cached catalog stale (refetched on next use) without discarding it as a fallback."""
    global _cache
    with _lock:
        if _cache is not None:
            _cache = (float("-inf"), _cache[1])


def request(method: str, service: str, path: str, **kwargs) -> requests.Response:
    kwargs.setdefault("timeout", REQUEST_TIMEOUT)
    base, overridden = _resolve(service, "internal")
    try:
        return requests.request(method, base + path, **kwargs)
    except requests.ConnectionError as original:
        if overridden:
            raise
        _expire()
        try:
            fresh, _ = _resolve(service, "internal")
        except CatalogError:
            raise original from None
        if fresh == base:
            raise
    return requests.request(method, fresh + path, **kwargs)
