"""
cloudx shared request-id middleware
=====================================
Adds 'x-openstack-request-id' header to every response,
mirroring the OpenStack standard for request tracing.

Usage in a FastAPI service:

    from app.request_id_middleware import RequestIDMiddleware
    app.add_middleware(RequestIDMiddleware)
"""
import uuid
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        req_id = f"req-{uuid.uuid4()}"
        response = await call_next(request)
        response.headers["x-openstack-request-id"] = req_id
        return response
