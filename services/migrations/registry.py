"""Where each service keeps its schema.

Every service has its own migration history in services/<service>/migrations/versions and its own version table
(alembic_version_<service>), so services can share one database or use separate ones: the database URL is resolved
exactly as the service resolves it at runtime (its /etc/cloudx conf file, then the env vars below, then the default).

service dir -> (conf name under /etc/cloudx, DB URL env vars in priority order, default database, models module)
"""
DEFAULT_URL = "postgresql+psycopg://cloudx:cloudx@127.0.0.1:5432/{db}"

SERVICES = {
    "auth":          ("auth-api",          ["DATABASE_URL"],                                  "cloudx",               "services.auth.app.models"),
    "billing":       ("billing-api",       ["BILLING_DATABASE_URL", "DATABASE_URL"],          "cloudx_billing",       "services.billing.app.main"),
    "block_store":   ("block-store-api",   ["DATABASE_URL"],                                  "cloudx",               "services.block_store.app.models"),
    "compute":       ("compute-api",       ["DATABASE_URL"],                                  "cloudx",               "services.compute.app.models"),
    "dns":           ("dns-api",           ["DATABASE_URL"],                                  "cloudx",               "services.dns.app.main"),
    "event":         ("event-api",         ["EVENT_DATABASE_URL", "DATABASE_URL"],            "cloudx_events",        "services.event.app.main"),
    "file_store":    ("file-store-api",    ["DATABASE_URL"],                                  "cloudx",               "services.file_store.app.main"),
    "container":     ("container-api",     ["DATABASE_URL"],                                  "cloudx",               "services.container.app.main"),
    "database":      ("database-api",      ["DATABASE_URL"],                                  "cloudx",               "services.database.app.main"),
    "ha":            ("ha-api",            ["DATABASE_URL"],                                  "cloudx",               "services.ha.app.main"),
    "image":         ("image-api",         ["DATABASE_URL"],                                  "cloudx",               "services.image.app.main"),
    "k8s_engine":    ("k8s-engine-api",    ["DATABASE_URL"],                                  "cloudx_k8s_engine",    "services.k8s_engine.app.main"),
    "key_manager":   ("key-manager-api",   ["KEY_MANAGER_DATABASE_URL", "DATABASE_URL"],      "cloudx_key_manager",   "services.key_manager.app.main"),
    "load_balancer": ("load-balancer-api", ["DATABASE_URL"],                                  "cloudx",               "services.load_balancer.app.main"),
    "networking":    ("networking-api",    ["DATABASE_URL"],                                  "cloudx",               "services.networking.app.models"),
    "object_store":  ("object-store-api",  ["DATABASE_URL"],                                  "cloudx",               "services.object_store.app.main"),
    "orchestration": ("orchestration-api", ["ORCHESTRATION_DATABASE_URL", "DATABASE_URL"],    "cloudx_orchestration", "services.orchestration.app.main"),
    "placement":     ("placement-api",     ["DATABASE_URL"],                                  "cloudx",               "services.placement.app.main"),
    "telemetry":     ("telemetry-api",     ["DATABASE_URL"],                                  "cloudx",               "services.telemetry.app.main"),
}
