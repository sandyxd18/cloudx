"""
cloudx shared Prometheus metrics
=================================
Lightweight per-service instrumentation. Call `setup_metrics(app, "compute-api")`
after creating the FastAPI app; it adds:

  • a request counter + latency histogram labelled by method/path/status
  • a GET /metrics endpoint in Prometheus text format

Import is optional/defensive: if prometheus_client is missing the helper becomes
a no-op so services still boot.
"""
from __future__ import annotations

import time

try:
    from prometheus_client import Counter, Histogram, CONTENT_TYPE_LATEST, generate_latest
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request
    from starlette.responses import Response
    _AVAILABLE = True
except Exception:  # prometheus_client not installed
    _AVAILABLE = False


if _AVAILABLE:
    REQUEST_COUNT = Counter(
        "cloudx_api_requests_total",
        "Total API requests",
        ["service", "method", "path", "status"],
    )
    REQUEST_LATENCY = Histogram(
        "cloudx_api_request_latency_seconds",
        "API request latency in seconds",
        ["service", "method", "path"],
    )

    class _MetricsMiddleware(BaseHTTPMiddleware):
        def __init__(self, app, service: str):
            super().__init__(app)
            self.service = service

        async def dispatch(self, request: Request, call_next):
            # Use the route template (not the raw path) to keep cardinality low.
            start = time.perf_counter()
            response = await call_next(request)
            elapsed = time.perf_counter() - start
            route = request.scope.get("route")
            path = getattr(route, "path", request.url.path)
            REQUEST_COUNT.labels(self.service, request.method, path, str(response.status_code)).inc()
            REQUEST_LATENCY.labels(self.service, request.method, path).observe(elapsed)
            return response


def setup_metrics(app, service: str) -> None:
    """Attach the metrics middleware + /metrics endpoint. No-op if unavailable."""
    if not _AVAILABLE:
        return

    app.add_middleware(_MetricsMiddleware, service=service)

    @app.get("/metrics")
    def metrics():  # noqa: ANN202
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
