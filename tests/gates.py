"""Helpers that pin which policy action gates which route of a service app."""
import re

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient


def _action_of(dependant) -> str | None:
    for dep in dependant.dependencies:
        action = getattr(dep.call, "action", None)
        if action is not None:
            return action
        found = _action_of(dep)
        if found is not None:
            return found
    return None


def route_actions(app) -> dict[tuple[str, str], str]:
    """(METHOD, path) -> action, for every route that has a policy.require() dependency anywhere in its tree."""
    found: dict[tuple[str, str], str] = {}
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        action = _action_of(route.dependant)
        if action is None:
            continue
        for method in route.methods - {"HEAD", "OPTIONS"}:
            found[(method, route.path)] = action
    return found


def assert_gated(app, who: dict, routes) -> None:
    """Each (METHOD, path_template, action) answers 403 to a member, a domain admin and a reader, and not 403 to an admin.

    `who` is the dict the test's require_auth override returns; it is changed in place (and restored).
    """
    client = TestClient(app, raise_server_exceptions=False)
    saved = dict(who)
    try:
        for method, template, action in routes:
            path = re.sub(r"\{[^}]+\}", "x", template)
            expected = {"detail": f"Policy does not allow {action}"}
            for label, fields, body_checked in (
                ("member", {"role": "member", "domain_admin": False}, True),
                ("domain admin", {"role": "member", "domain_admin": True}, True),
                ("reader", {"role": "reader", "domain_admin": False}, False),
            ):
                who.clear()
                who.update(saved, **fields)
                r = client.request(method, path, json={})
                assert r.status_code == 403, (method, template, label, r.status_code, r.text)
                if body_checked:
                    assert r.json() == expected, (method, template, label, r.text)
            who.clear()
            who.update(saved, role="admin", domain_admin=False)
            r = client.request(method, path, json={})
            assert r.status_code != 403, (method, template, "admin", r.text)
    finally:
        who.clear()
        who.update(saved)
