"""
cloudx central policy engine
==============================
Provides a simple rule-based RBAC engine.
"""

from __future__ import annotations

from fastapi import HTTPException

# Policy rules map: "service:resource:action" → rule_name
# Rules: "admin_required", "admin_or_owner", "", "deny_all"
POLICY_RULES: dict[str, str] = {
    "identity:create_project": "admin_required",
    "identity:delete_project": "admin_required",
    "identity:list_projects":  "admin_required",
    "identity:create_user":     "admin_required",
    "identity:delete_user":     "admin_required",
    "identity:list_users":      "admin_required",
    "identity:create_domain":   "admin_required",
    "identity:delete_domain":   "admin_required",
    "identity:assign_role":     "admin_required",
    "identity:revoke_role":     "admin_required",
    "compute:flavor:create":    "admin_required",
    "compute:flavor:delete":    "admin_required",
    "image:deactivate":         "admin_required",
    "image:reactivate":         "admin_required",
    "volume:type:create":       "admin_required",
    "volume:type:delete":       "admin_required",
    "volume:quota:set":         "admin_required",
}


def _get_rule(action: str) -> str:
    return POLICY_RULES.get(action, "")


def enforce(
    action: str,
    token_payload: dict,
    target_project_id: str | None = None,
    raise_on_deny: bool = True,
) -> bool:
    rule = _get_rule(action)
    role = token_payload.get("role", "")
    token_project = token_payload.get("project_id", "")
    resource_project = target_project_id or token_project

    allowed = False
    if rule == "" or role == "admin":
        allowed = True
    elif rule == "admin_or_owner":
        allowed = token_project == resource_project

    if not allowed and raise_on_deny:
        detail = f"Policy violation: '{action}' requires admin role" if rule == "admin_required" else f"Policy violation: not authorized for '{action}'"
        raise HTTPException(status_code=403, detail=detail)

    return allowed


def is_admin(token_payload: dict) -> bool:
    return token_payload.get("role") == "admin"


def list_rules() -> dict[str, str]:
    return dict(POLICY_RULES)
