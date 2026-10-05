"""
cloudx Shared Inter-Service HTTP Client
=========================================
Handles inter-service HTTP requests with automatic auth token caching,
proactive token refresh, structured exception handling, and request-id forwarding.

Usage in a service:

    from shared.service_client import ServiceClient

    client = ServiceClient(service_name="service_compute")
    data = client.get("http://networking-api:8000/ports")
"""

import logging
import os
import time
from typing import Any

import requests

log = logging.getLogger("cloudx-service-client")

AUTH_API_URL = os.getenv("AUTH_API_URL", "http://auth-api:8000").rstrip("/")
AUTH_ADMIN_USERNAME = os.getenv("AUTH_ADMIN_USERNAME", "admin")
AUTH_ADMIN_PASSWORD = os.environ["AUTH_ADMIN_PASSWORD"]


class ServiceClient:
    def __init__(self, service_name: str = "service_system"):
        self.service_name = service_name
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    def _get_token(self) -> str | None:
        now = time.time()
        # Token valid with 60s buffer
        if self._token and now < (self._token_expires_at - 60):
            return self._token

        try:
            resp = requests.post(
                f"{AUTH_API_URL}/auth/tokens",
                json={"username": AUTH_ADMIN_USERNAME, "password": AUTH_ADMIN_PASSWORD},
                timeout=10,
            )
            if resp.status_code == 201:
                data = resp.json()
                self._token = data.get("token")
                expires_in = data.get("expires_in", 86400)
                self._token_expires_at = now + expires_in
                log.info("[%s] service token refreshed successfully", self.service_name)
            else:
                log.warning("[%s] failed to obtain service token: HTTP %s", self.service_name, resp.status_code)
        except Exception as exc:
            log.warning("[%s] auth-api unreachable for service token: %s", self.service_name, exc)

        return self._token

    def _headers(self, custom_headers: dict | None = None) -> dict:
        headers = {"Content-Type": "application/json"}
        token = self._get_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
            headers["X-Auth-Token"] = token
        if custom_headers:
            headers.update(custom_headers)
        return headers

    def request(self, method: str, url: str, **kwargs) -> Any:
        headers = self._headers(kwargs.pop("headers", None))
        timeout = kwargs.pop("timeout", 15)

        try:
            resp = requests.request(method, url, headers=headers, timeout=timeout, **kwargs)
        except requests.RequestException as exc:
            log.error("[%s] HTTP %s request to %s failed: %s", self.service_name, method, url, exc)
            raise

        if resp.status_code == 401:
            log.warning("[%s] HTTP 401 from %s, clearing token & retrying", self.service_name, url)
            self._token = None
            headers = self._headers()
            resp = requests.request(method, url, headers=headers, timeout=timeout, **kwargs)

        return resp

    def get(self, url: str, **kwargs) -> requests.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> requests.Response:
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs) -> requests.Response:
        return self.request("PUT", url, **kwargs)

    def delete(self, url: str, **kwargs) -> requests.Response:
        return self.request("DELETE", url, **kwargs)


# Global singleton instance for convenient imports
service_client = ServiceClient()
