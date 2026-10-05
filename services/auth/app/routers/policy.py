from fastapi import APIRouter, HTTPException
from shared.policy import enforce as policy_enforce
from shared.policy import list_rules as policy_list_rules

from ..deps import CurrentUser

router = APIRouter()


# ── Central Policy (oslo.policy) ───────────────────────────────────────────────

@router.get("/policy")
def get_policy(_: CurrentUser):
    """Return the central RBAC policy map for all CloudX services."""
    return {"policy": policy_list_rules()}


@router.post("/policy/enforce")
def check_policy(body: dict, current: CurrentUser):
    """Check if the current user can perform an action (for debugging/UI)."""
    action = body.get("action", "")
    if not action:
        raise HTTPException(status_code=400, detail="'action' is required")
    target_project = body.get("target_project_id")
    result = policy_enforce(action, current, target_project, raise_on_deny=False)
    return {"action": action, "allowed": result, "role": current.get("role")}
