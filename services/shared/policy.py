"""
cloudx central policy engine (oslo.policy-inspired)
=====================================================

Provides a simple rule-based RBAC engine that reads from a central
policy map. Each service can enforce rules via:

    from shared.policy import enforce

    enforce("compute:server:create", token_payload)

Rules are defined in `policy_rules` dict and can be:
    - "admin_required"  : role == "admin"
    - "admin_or_owner"  : role == "admin" OR project_id matches
    - ""                : any authenticated user (open)
    - "deny_all"        : always deny

Default rule (if rule not found): any authenticated user can access.
"""

from __future__ import annotations

from fastapi import HTTPException


# ── Central Policy Map ─────────────────────────────────────────────────────────
# Format: "service:resource:action" → rule_name
# Rules:
#   "admin_required"  — only admin role
#   "admin_or_owner"  — admin OR project owner
#   ""                — any authenticated user
#   "deny_all"        — never allow

POLICY_RULES: dict[str, str] = {
    # ── Keystone / Identity ────────────────────────────────────────────────────
    "identity:create_project":       "admin_required",
    "identity:delete_project":       "admin_required",
    "identity:list_projects":        "admin_required",
    "identity:get_project":          "admin_or_owner",
    "identity:create_user":          "admin_required",
    "identity:delete_user":          "admin_required",
    "identity:list_users":           "admin_required",
    "identity:get_user":             "admin_or_owner",
    "identity:update_user":          "admin_or_owner",
    "identity:create_domain":        "admin_required",
    "identity:delete_domain":        "admin_required",
    "identity:list_domains":         "",
    "identity:get_domain":           "",
    "identity:list_roles":           "",
    "identity:assign_role":          "admin_required",
    "identity:revoke_role":          "admin_required",
    "identity:list_services":        "",
    "identity:manage_services":      "admin_required",
    "identity:create_app_credential":  "",
    "identity:list_app_credentials":   "admin_or_owner",
    "identity:delete_app_credential":  "admin_or_owner",

    # ── Nova / Compute ─────────────────────────────────────────────────────────
    "compute:server:create":         "",
    "compute:server:delete":         "admin_or_owner",
    "compute:server:list":           "",
    "compute:server:get":            "admin_or_owner",
    "compute:server:action":         "admin_or_owner",
    "compute:server:lock":           "admin_or_owner",
    "compute:server:unlock":         "admin_or_owner",
    "compute:server:shelve":         "admin_or_owner",
    "compute:server:unshelve":       "admin_or_owner",
    "compute:flavor:create":         "admin_required",
    "compute:flavor:delete":         "admin_required",
    "compute:flavor:list":           "",
    "compute:keypair:create":        "",
    "compute:keypair:delete":        "admin_or_owner",
    "compute:keypair:list":          "",
    "compute:aggregate:create":      "admin_required",
    "compute:aggregate:list":        "",
    "compute:aggregate:get":         "",
    "compute:aggregate:update":      "admin_required",
    "compute:aggregate:delete":      "admin_required",
    "compute:aggregate:add_host":    "admin_required",
    "compute:aggregate:remove_host": "admin_required",

    # ── Neutron / Networking ────────────────────────────────────────────────────
    "network:create":                "",
    "network:delete":                "admin_or_owner",
    "network:list":                  "",
    "network:get":                   "",
    "subnet:create":                 "",
    "subnet:delete":                 "admin_or_owner",
    "subnet:list":                   "",
    "router:create":                 "",
    "router:delete":                 "admin_or_owner",
    "router:list":                   "",
    "port:create":                   "",
    "port:delete":                   "admin_or_owner",
    "port:list":                     "",
    "floatingip:create":             "",
    "floatingip:delete":             "admin_or_owner",
    "floatingip:list":               "",
    "security_group:create":         "",
    "security_group:delete":         "admin_or_owner",
    "security_group:list":           "",
    "qos:create":                    "",
    "qos:delete":                    "admin_or_owner",
    "qos:list":                      "",
    "port_forwarding:create":        "",
    "port_forwarding:delete":        "admin_or_owner",
    "port_forwarding:list":          "",
    "trunk:create":                  "",
    "trunk:delete":                  "admin_or_owner",
    "trunk:list":                    "",
    "trunk:get":                     "admin_or_owner",
    "trunk:add_subports":            "admin_or_owner",
    "trunk:remove_subports":         "admin_or_owner",

    # ── Glance / Image ─────────────────────────────────────────────────────────
    "image:create":                  "",
    "image:delete":                  "admin_or_owner",
    "image:list":                    "",
    "image:get":                     "",
    "image:upload":                  "admin_or_owner",
    "image:deactivate":              "admin_required",
    "image:reactivate":              "admin_required",
    "image:share":                   "admin_or_owner",
    "image:sign":                    "admin_or_owner",
    "image:verify":                  "",

    # ── Cinder / Block Storage ─────────────────────────────────────────────────
    "volume:create":                 "",
    "volume:delete":                 "admin_or_owner",
    "volume:list":                   "",
    "volume:get":                    "admin_or_owner",
    "volume:extend":                 "admin_or_owner",
    "volume:attach":                 "admin_or_owner",
    "volume:detach":                 "admin_or_owner",
    "volume:snapshot":               "admin_or_owner",
    "volume:migrate":                "admin_or_owner",
    "volume:type:create":            "admin_required",
    "volume:type:delete":            "admin_required",
    "volume:type:list":              "",
    "volume:quota:get":              "admin_or_owner",
    "volume:quota:set":              "admin_required",

    # ── Placement ──────────────────────────────────────────────────────────────
    "placement:resource_provider:create": "admin_required",
    "placement:resource_provider:list":   "",
    "placement:inventory:set":            "admin_required",
    "placement:allocation:create":        "",
    "placement:allocation:delete":        "",
    "placement:usage:get":                "",
    "placement:resource_class:create":    "admin_required",
    "placement:resource_class:delete":    "admin_required",
    "placement:resource_class:list":      "",

    # ── Quota (Cross-cutting) ──────────────────────────────────────────────────
    "quota:get":                     "admin_or_owner",
    "quota:set":                     "admin_required",
    "quota:reserve":                 "",
}


def _get_rule(action: str) -> str:
    """Look up the rule for an action. Default: open (any authenticated user)."""
    return POLICY_RULES.get(action, "")


def enforce(
    action: str,
    token_payload: dict,
    target_project_id: str | None = None,
    raise_on_deny: bool = True,
) -> bool:
    """
    Enforce a policy rule for the given action.

    Args:
        action: Policy action string e.g. "compute:server:create"
        token_payload: JWT payload dict (must have 'role' and 'project_id')
        target_project_id: The project_id of the resource being accessed
                           (for owner checks). If None, uses token's project_id.
        raise_on_deny: If True, raises HTTPException(403) on denial.

    Returns:
        True if allowed, False if denied (only when raise_on_deny=False).
    """
    rule = _get_rule(action)
    role = token_payload.get("role", "")
    token_project = token_payload.get("project_id", "")
    resource_project = target_project_id or token_project

    allowed = False

    if rule == "":
        # Any authenticated user
        allowed = True
    elif rule == "admin_required":
        allowed = role == "admin"
    elif rule == "admin_or_owner":
        allowed = role == "admin" or token_project == resource_project
    elif rule == "deny_all":
        allowed = False
    else:
        # Unknown rule — default deny for safety
        allowed = False

    if not allowed and raise_on_deny:
        if role != "admin" and rule == "admin_required":
            detail = f"Policy violation: '{action}' requires admin role"
        else:
            detail = f"Policy violation: not authorized for '{action}'"
        raise HTTPException(status_code=403, detail=detail)

    return allowed


def is_admin(token_payload: dict) -> bool:
    """Convenience check: is the token holder an admin?"""
    return token_payload.get("role") == "admin"


def list_rules() -> dict[str, str]:
    """Return the full policy map (for GET /policy endpoint)."""
    return dict(POLICY_RULES)


# ── FastAPI Dependency Factory ────────────────────────────────────────────────

from fastapi import Depends  # noqa: E402 (import kept local to avoid circular)


def require_policy(action: str, target_project_id: str | None = None):
    """
    FastAPI Dependency factory for policy enforcement.

    Usage:
        @app.delete("/servers/{id}")
        def delete_server(id: str, _: Annotated[dict, Depends(require_policy("compute:server:delete"))]):
            ...

    For owner-based rules, wrap with a function that passes target_project_id:
        def check_policy(current=Depends(require_auth)):
            enforce("compute:server:delete", current, target_project_id=server.project_id)
    """
    def _dependency(current: dict = Depends(require_auth)) -> dict:
        enforce(action, current, target_project_id=target_project_id, raise_on_deny=True)
        return current
    return _dependency


# Import here to avoid circular dependency (auth_middleware imports jose, not policy)
try:
    from app.auth_middleware import require_auth  # type: ignore[import]
except ImportError:
    # Fallback for tests or direct imports from shared module
    def require_auth(request):  # type: ignore[misc]
        raise NotImplementedError("auth_middleware not available")
