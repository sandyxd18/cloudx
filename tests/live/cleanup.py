"""Remove what the live tests leave behind in the lab (they create resources with recognisable names).

Runs automatically at the end of a `--live` session (see conftest.py). To clean a lab by hand:

    AUTH_ADMIN_PASSWORD=... python -c "from tests.live.cleanup import sweep; print(sweep())"
"""
import os
import re

import requests

URLS = {
    "auth": os.getenv("AUTH_API_URL", "http://127.0.0.1:8005"),
    "networking": os.getenv("NETWORKING_API_URL", "http://127.0.0.1:8001"),
    "compute": os.getenv("COMPUTE_API_URL", "http://127.0.0.1:8003"),
    "image": os.getenv("IMAGE_API_URL", "http://127.0.0.1:8002"),
    "placement": os.getenv("PLACEMENT_API_URL", "http://127.0.0.1:8006"),
    "orchestration": os.getenv("ORCHESTRATION_API_URL", "http://127.0.0.1:8013"),
    "billing": os.getenv("BILLING_API_URL", "http://127.0.0.1:8011"),
}
PROVIDER = re.compile(r"(compute-node|sched-test|heartbeat-test)-[0-9a-f]{6}")


def _items(data, *keys):
    if isinstance(data, list):
        return data
    for key in (*keys, "items"):
        if isinstance(data, dict) and isinstance(data.get(key), list):
            return data[key]
    return []


def sweep(token: str | None = None) -> dict:
    """Delete leftovers by name pattern; returns {kind: count}. Never touches anything without a test-style name."""
    if token is None:
        r = requests.post(f"{URLS['auth']}/auth/tokens", timeout=10,
                          json={"username": os.getenv("AUTH_ADMIN_USERNAME", "admin"), "password": os.environ["AUTH_ADMIN_PASSWORD"]})
        token = r.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    removed: dict[str, int] = {}

    def get(url, *keys):
        res = requests.get(url, headers=headers, timeout=15)
        return _items(res.json(), *keys) if res.status_code == 200 else []

    def delete(kind, url):
        if requests.delete(url, headers=headers, timeout=20).status_code < 300:
            removed[kind] = removed.get(kind, 0) + 1

    n, c, i, p, o = (URLS[k] for k in ("networking", "compute", "image", "placement", "orchestration"))

    routers = [r for r in get(f"{n}/routers", "routers") if r["name"].startswith(("test-router-", "peer-router-"))]
    router_ids = {r["id"] for r in routers}
    for peering in get(f"{n}/vpc-peerings"):
        if {peering.get("requester_router_id"), peering.get("accepter_router_id")} & router_ids:
            delete("vpc-peerings", f"{n}/vpc-peerings/{peering['id']}")
    for peer in get(f"{n}/bgp-peers"):
        if peer.get("router_id") in router_ids:
            delete("bgp-peers", f"{n}/bgp-peers/{peer['id']}")
    for r in routers:
        delete("routers", f"{n}/routers/{r['id']}")
    for group in get(f"{o}/scaling-groups"):
        if group["name"].startswith("test-asg-"):
            delete("scaling-groups", f"{o}/scaling-groups/{group['id']}")
    for rp in get(f"{p}/resource_providers", "resource_providers"):
        if PROVIDER.fullmatch(rp["name"]):
            delete("resource-providers", f"{p}/resource_providers/{rp.get('uuid') or rp['id']}")
    for kind in ("ports", "subnets"):      # a network cannot be deleted while it still has either
        for item in get(f"{n}/{kind}", kind):
            if item["name"].startswith("pytest-"):
                delete(kind, f"{n}/{kind}/{item['id']}")
    for net in get(f"{n}/networks", "networks"):
        if net["name"].startswith("pytest-"):
            delete("networks", f"{n}/networks/{net['id']}")
    for key in get(f"{c}/keypairs", "keypairs"):
        if key["name"].startswith("pytest-key"):
            delete("keypairs", f"{c}/keypairs/{key['name']}")
    for img in get(f"{i}/images", "images"):
        if img["name"].startswith("pytest-image"):
            delete("images", f"{i}/images/{img['id']}")
    # the billing API has no delete: switch the test rating sheet ("gpu") off instead
    for sheet in get(f"{URLS['billing']}/billing/rating-sheets?active_only=true"):
        if sheet.get("resource_type") == "gpu":
            res = requests.patch(f"{URLS['billing']}/billing/rating-sheets/{sheet['id']}", headers=headers, timeout=15,
                                 json={"resource_type": "gpu", "unit": sheet["unit"], "rate_per_unit": sheet["rate_per_unit"],
                                       "currency": sheet.get("currency", "IDR"), "is_active": False})
            if res.status_code < 300:
                removed["rating-sheets"] = removed.get("rating-sheets", 0) + 1
    return removed
