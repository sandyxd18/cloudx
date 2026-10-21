"""Automatic platform names (AWS style) and the best-effort client other services use to register them in dns-api."""
import logging
import os
import re
import unicodedata

import requests

from shared import catalog
from shared.internal import internal_headers

log = logging.getLogger("dns-names")


def slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:40].strip("-") or "x"


def fip_fqdn(ip: str, base: str) -> str:
    return f"{ip.replace('.', '-').replace(':', '-')}.compute.{base}"


def lb_fqdn(name: str, lb_id: str, base: str) -> str:
    return f"{slug(name)}-{lb_id[:6]}.elb.{base}"


def bucket_fqdn(bucket: str, project_id: str, base: str) -> str:
    return f"{slug(bucket)}-{project_id[:8]}.s3.{base}"


def internal_fqdn(name: str, project: str, base: str) -> str:
    return f"{slug(name)}.{slug(project)}.internal.{base}"


def base() -> str:
    return os.getenv("DNS_PLATFORM_DOMAIN", "cloud.lab").strip().lower().rstrip(".")


def public_address() -> str:
    return os.getenv("DNS_PUBLIC_ADDRESS", "172.20.0.120")


def _url() -> str:
    return catalog.url("dns").rstrip("/")


def register(owner: str, project_id: str, fqdn: str, rtype: str, records: list[str], scope: str = "public") -> None:
    """Create or replace the record owned by `owner` (e.g. 'floating_ip:<id>'). Never raises: DNS must not fail a create."""
    try:
        requests.post(f"{_url()}/internal/records", headers=internal_headers(), timeout=3, json={
            "owner": owner, "project_id": project_id, "fqdn": fqdn, "type": rtype, "records": records, "scope": scope,
        }).raise_for_status()
    except Exception as exc:  # best-effort: the dns sweep repairs a missed registration
        log.warning("dns register %s failed: %s", owner, exc)


def unregister(owner: str) -> None:
    try:
        requests.delete(f"{_url()}/internal/records", params={"owner": owner}, headers=internal_headers(), timeout=3).raise_for_status()
    except Exception as exc:  # best-effort: the dns sweep removes orphans
        log.warning("dns unregister %s failed: %s", owner, exc)
