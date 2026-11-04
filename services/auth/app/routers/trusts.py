import json
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from jose import jwt
from sqlalchemy import or_, select, update

from ..db import DB
from ..deps import CurrentUser
from ..lookups import _get_project_by_ref, _get_user_by_ref
from ..models import AuditLog, Project, Trust, User, utcnow
from ..schemas import ROLES, TrustIn, TrustTokenIn
from ..tokens import _create_token, role_held
from .tokens import _check_domain, _member_projects

router = APIRouter()

def _trust_to_dict(t: Trust) -> dict:
    return {
        "id":              t.id,
        "trustor_user_id": t.trustor_user_id,
        "trustee_user_id": t.trustee_user_id,
        "project_id":      t.project_id,
        "roles":           t.roles.split(","),
        "expires_at":      t.expires_at.isoformat() if t.expires_at else None,
        "remaining_uses":  t.remaining_uses,
        "created_at":      t.created_at.isoformat(),
    }


def _parse_expires_at(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail="expires_at must be a valid ISO 8601 datetime")
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    if dt <= utcnow():
        raise HTTPException(status_code=400, detail="expires_at must be in the future")
    return dt


def _consume_use(db, trust_id: str) -> bool:
    """Take one use. The WHERE clause makes the check and the decrement a single statement, so two concurrent
    redemptions of the last use cannot both succeed. The caller commits."""
    res = db.execute(
        update(Trust).where(Trust.id == trust_id, Trust.remaining_uses > 0)
        .values(remaining_uses=Trust.remaining_uses - 1)
    )
    return res.rowcount == 1


@router.post("/v3/OS-TRUST/trusts", status_code=201)
def create_trust(payload: TrustIn, current: CurrentUser, db: DB):
    """Delegate some of the caller's roles in one project to another user."""
    trustor = db.get(User, current["sub"])
    if not trustor:
        raise HTTPException(status_code=401, detail="user not found")
    # the same meaning of "admin" as when the trust is redeemed: the user's own role, not the token's role in a project
    is_admin = trustor.role == "admin"
    domain = None if is_admin else trustor.domain_id

    ref = payload.project_id or current.get("project_id")
    if not ref:
        raise HTTPException(status_code=400, detail="project_id required for a token without a project")
    proj = _get_project_by_ref(db, ref, domain)
    if not is_admin:
        _check_domain(db, trustor, proj)
        if proj.id not in _member_projects(db, trustor):
            raise HTTPException(status_code=403, detail="you are not a member of that project")

    roles = payload.roles
    if not roles or len(set(roles)) != len(roles) or any(r not in ROLES for r in roles):
        raise HTTPException(status_code=400, detail=f"roles must be a non-empty list without duplicates from: {', '.join(ROLES)}")
    for r in roles:
        if not role_held(db, trustor, proj.id, r):
            raise HTTPException(status_code=403, detail=f"you do not hold the {r} role in this project")

    trustee = _get_user_by_ref(db, payload.trustee_user_id, domain)
    if trustee.id == trustor.id:
        raise HTTPException(status_code=400, detail="the trustee must be another user")
    if not trustee.enabled or (not is_admin and trustee.domain_id != trustor.domain_id):
        raise HTTPException(status_code=403, detail="trustee not allowed")

    t = Trust(
        trustor_user_id=trustor.id,
        trustee_user_id=trustee.id,
        project_id=proj.id,
        roles=",".join(roles),
        expires_at=_parse_expires_at(payload.expires_at),
        remaining_uses=payload.remaining_uses,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return _trust_to_dict(t)


@router.get("/v3/OS-TRUST/trusts")
def list_trusts(current: CurrentUser, db: DB):
    """Trusts the caller gave or received (an admin sees all). Revoked trusts are hidden."""
    q = select(Trust).where(Trust.revoked == False).order_by(Trust.created_at)  # noqa: E712
    if current.get("role") != "admin":
        q = q.where(or_(Trust.trustor_user_id == current["sub"], Trust.trustee_user_id == current["sub"]))
    return {"trusts": [_trust_to_dict(t) for t in db.scalars(q).all()]}


def _visible_trust(db, trust_id: str, current: dict, *, only_trustor: bool = False) -> Trust:
    t = db.get(Trust, trust_id)
    if not t or t.revoked:
        raise HTTPException(status_code=404, detail="trust not found")
    if current.get("role") == "admin" or current["sub"] == t.trustor_user_id:
        return t
    if only_trustor:
        raise HTTPException(status_code=403, detail="only the trustor or an admin may do this")
    if current["sub"] != t.trustee_user_id:
        raise HTTPException(status_code=404, detail="trust not found")
    return t


@router.get("/v3/OS-TRUST/trusts/{trust_id}")
def show_trust(trust_id: str, current: CurrentUser, db: DB):
    return _trust_to_dict(_visible_trust(db, trust_id, current))


@router.delete("/v3/OS-TRUST/trusts/{trust_id}", status_code=204)
def delete_trust(trust_id: str, current: CurrentUser, db: DB):
    """Revoke a trust: tokens already issued from it stay valid until they expire."""
    t = _visible_trust(db, trust_id, current, only_trustor=True)
    t.revoked = True
    db.commit()


def _refuse(detail: str):
    return HTTPException(status_code=401, detail=detail)


@router.post("/auth/tokens/trust", status_code=201)
def login_with_trust(payload: TrustTokenIn, current: CurrentUser, db: DB):
    """The trustee exchanges a trust for a token that acts as the trustor, limited to the roles the trust lists
    and the trustor still holds."""
    t = db.get(Trust, payload.trust_id)
    if not t:
        raise _refuse("invalid trust")    # unknown and unusable look the same: no id oracle
    if current["sub"] != t.trustee_user_id:
        raise HTTPException(status_code=403, detail="this trust was not granted to you")
    if t.revoked:
        raise _refuse("trust revoked")
    if t.expires_at and t.expires_at <= utcnow():
        raise _refuse("trust expired")
    if t.remaining_uses is not None and t.remaining_uses <= 0:
        raise _refuse("trust has no uses left")
    trustor = db.get(User, t.trustor_user_id)
    if not trustor or not trustor.enabled:
        raise _refuse("trustor unavailable")
    proj = db.get(Project, t.project_id)
    if not proj or not proj.enabled:
        raise _refuse("project unavailable")
    try:
        _check_domain(db, trustor, proj)
    except HTTPException as e:
        raise _refuse(str(e.detail))
    if trustor.role != "admin" and proj.id not in _member_projects(db, trustor):
        raise _refuse("trustor is no longer a member of the project")
    granted = [r for r in t.roles.split(",") if role_held(db, trustor, proj.id, r)]
    if not granted:
        raise _refuse("trustor no longer holds any of the trust roles")

    # last, so a request that fails a check above never spends a use
    if t.remaining_uses is not None and not _consume_use(db, t.id):
        raise _refuse("trust has no uses left")

    token = _create_token(
        trustor, project_name=proj.name, extra_roles=granted,
        project_id=proj.id, trust_id=t.id, trustee_id=t.trustee_user_id,
        domain_admin=False,   # the trustor's domain-admin right is not part of what a trust delegates
        expires_at=t.expires_at,   # a token never outlives the trust it came from
    )
    issued = jwt.get_unverified_claims(token)
    expires_in = max(0, int(issued["exp"] - issued["iat"]))   # the real lifetime of this token, not the default
    db.add(AuditLog(
        user_id=current["sub"], username=current.get("username"), project_id=proj.id,
        action="trust.token", resource_type="trust", resource_id=t.id,
        details=json.dumps({"trustor_user_id": t.trustor_user_id, "trustee_user_id": t.trustee_user_id}),
    ))
    db.commit()
    return {"token": token, "expires_in": expires_in, "method": "trust"}
