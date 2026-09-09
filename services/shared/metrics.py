"""
cloudx shared Prometheus metrics instrumentation.
"""
from __future__ import annotations

import time
from prometheus_client import Counter, Histogram, CONTENT_TYPE_LATEST, generate_latest
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

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
        start = time.perf_counter()
        response = await call_next(request)
        elapsed = time.perf_counter() - start
        route = request.scope.get("route")
        path = getattr(route, "path", request.url.path)
        REQUEST_COUNT.labels(self.service, request.method, path, str(response.status_code)).inc()
        REQUEST_LATENCY.labels(self.service, request.method, path).observe(elapsed)
        return response


def setup_metrics(app, service: str) -> None:
    app.add_middleware(_MetricsMiddleware, service=service)

    @app.get("/metrics")
    def metrics():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
