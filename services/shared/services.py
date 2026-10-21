"""The CloudX service catalog: one row per service, shared by auth (bootstrap, /internal/catalog) and the clients."""
from dataclasses import dataclass


@dataclass(frozen=True)
class CatalogService:
    name: str
    type: str
    url_key: str
    port: int


SERVICES: tuple[CatalogService, ...] = (
    CatalogService("identity", "identity", "auth_api_url", 8005),
    CatalogService("compute", "compute", "compute_api_url", 8003),
    CatalogService("image", "image", "image_api_url", 8002),
    CatalogService("network", "network", "networking_api_url", 8001),
    CatalogService("volume", "block-storage", "block_store_api_url", 8004),
    CatalogService("placement", "placement", "placement_api_url", 8006),
    CatalogService("object-store", "object-store", "object_store_api_url", 8007),
    CatalogService("load-balancer", "load-balancer", "load_balancer_api_url", 8008),
    CatalogService("metadata", "metadata", "metadata_api_url", 8775),
    CatalogService("telemetry", "telemetry", "telemetry_api_url", 8010),
    CatalogService("billing", "billing", "billing_api_url", 8011),
    CatalogService("event", "event", "event_api_url", 8012),
    CatalogService("orchestration", "orchestration", "orchestration_api_url", 8013),
    CatalogService("key-manager", "key-manager", "key_manager_api_url", 8014),
    CatalogService("k8s-engine", "k8s-engine", "k8s_engine_api_url", 8015),
    CatalogService("dns", "dns", "dns_api_url", 8016),
    CatalogService("file-store", "file-store", "file_store_api_url", 8017),
    CatalogService("container", "container", "container_api_url", 8018),
    CatalogService("database", "database", "database_api_url", 8019),
    CatalogService("ha", "ha", "ha_api_url", 8020),
    CatalogService("messaging", "messaging", "messaging_api_url", 8022),
    CatalogService("reservation", "reservation", "reservation_api_url", 8023),
)

_BY_NAME = {s.name: s for s in SERVICES}


def by_name(name: str) -> CatalogService:
    return _BY_NAME[name]


def public_key(url_key: str) -> str:
    """`image_api_url` -> `image_api_public_url`: the key that overrides the URL advertised to users."""
    return url_key.removesuffix("_url") + "_public_url"
