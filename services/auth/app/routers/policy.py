from fastapi import APIRouter, HTTPException
from shared import policy

from ..deps import CurrentUser

router = APIRouter()


# ── Central Policy (shared.policy) ─────────────────────────────────────────────

@router.get("/policy")
def get_policy(_: CurrentUser):
    """The effective rule of every action and where it comes from ("default" or "file")."""
    rules, sources = policy.effective()
    return {"policy": rules, "sources": sources}


@router.post("/policy/enforce")
def check_policy(body: dict, current: CurrentUser):
    """Check if the current user can perform an action (for debugging/UI)."""
    action = body.get("action", "")
    if not action:
        raise HTTPException(status_code=400, detail="'action' is required")
    # body["target_project_id"] is still accepted but ignored: rules do not look at a target
    try:
        if not isinstance(action, str):
            raise KeyError(action)
        allowed = policy.authorize(action, current)
    except KeyError:
        raise HTTPException(status_code=400, detail=f"unknown action {action!r}") from None
    return {"action": action, "allowed": allowed, "role": current.get("role")}
