import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Body, HTTPException
from sqlalchemy import select

from ..config import TOKEN_EXPIRE_H
from ..db import DB
from ..deps import CurrentUser, scope_domain
from ..lookups import _get_user_by_ref
from ..models import ApplicationCredential, User, utcnow
from ..schemas import ROLES, AppCredIn, AppCredTokenIn, RotateIn
from ..tokens import _create_token, _hash_password, _verify_password, project_role, role_held

router = APIRouter()


# ── Application Credentials ──────────────────────────────────────────────────

def _appcred_to_dict(ac: "ApplicationCredential", secret: str | None = None) -> dict:
    d = {
        "id":          ac.id,
        "name":        ac.name,
        "description": ac.description,
        "user_id":     ac.user_id,
        "project_id":  ac.project_id,
        "roles":       ac.roles.split(",") if ac.roles else ["member"],
        "expires_at":  ac.expires_at.isoformat() if ac.expires_at else None,
        "enabled":     ac.enabled,
        "created_at":  ac.created_at.isoformat(),
    }
    if secret:
        d["secret"] = secret
    return d



def _credential_roles(db, user: User, requested: list[str] | None) -> list[str]:
    """Roles for a new credential: a default when none were asked for, otherwise exactly the roles the user holds."""
    if requested is None:
        return ["member"] if role_held(db, user, user.project_id, "member") else [project_role(db, user, user.project_id)]
    if not requested or len(set(requested)) != len(requested) or any(r not in ROLES for r in requested):
        raise HTTPException(status_code=400, detail=f"roles must be a non-empty list of distinct values from: {', '.join(ROLES)}")
    if not all(role_held(db, user, user.project_id, r) for r in requested):
        raise HTTPException(status_code=403, detail="a credential cannot carry a role its user does not hold")
    return requested


@router.post("/v3/users/{user_ref}/application_credentials", status_code=201)
def create_app_credential(user_ref: str, payload: AppCredIn, current: CurrentUser, db: DB):
    """Create an application credential for a user."""
    u = _get_user_by_ref(db, user_ref, scope_domain(current))
    # Users can only create creds for themselves; admins can do it for anyone
    if current.get("role") != "admin" and u.id != current["sub"]:
        raise HTTPException(status_code=403, detail="forbidden")
    if db.scalar(select(ApplicationCredential).where(
        ApplicationCredential.name == payload.name,
        ApplicationCredential.user_id == u.id,
    )):
        raise HTTPException(status_code=409, detail="application credential name already exists for this user")
    roles = _credential_roles(db, u, payload.roles)
    # Generate a random secret
    secret_plain = secrets.token_urlsafe(32)
    expires_dt: datetime | None = None
    if payload.expires_at:
        try:
            expires_dt = datetime.fromisoformat(payload.expires_at.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            raise HTTPException(status_code=400, detail="expires_at must be a valid ISO 8601 datetime")
    ac = ApplicationCredential(
        name=payload.name,
        description=payload.description,
        user_id=u.id,
        project_id=u.project_id,
        roles=",".join(roles),
        secret_hash=_hash_password(secret_plain),
        expires_at=expires_dt,
    )
    db.add(ac)
    db.commit()
    db.refresh(ac)
    return _appcred_to_dict(ac, secret=secret_plain)


@router.get("/v3/users/{user_ref}/application_credentials")
def list_app_credentials(user_ref: str, current: CurrentUser, db: DB):
    """List application credentials for a user."""
    u = _get_user_by_ref(db, user_ref, scope_domain(current))
    if current.get("role") != "admin" and u.id != current["sub"]:
        raise HTTPException(status_code=403, detail="forbidden")
    creds = db.scalars(
        select(ApplicationCredential).where(ApplicationCredential.user_id == u.id)
    ).all()
    return {"application_credentials": [_appcred_to_dict(c) for c in creds]}


@router.get("/v3/users/{user_ref}/application_credentials/{cred_ref}")
def show_app_credential(user_ref: str, cred_ref: str, current: CurrentUser, db: DB):
    """Show a specific application credential."""
    u = _get_user_by_ref(db, user_ref, scope_domain(current))
    if current.get("role") != "admin" and u.id != current["sub"]:
        raise HTTPException(status_code=403, detail="forbidden")
    ac = db.get(ApplicationCredential, cred_ref) or db.scalar(
        select(ApplicationCredential).where(
            ApplicationCredential.name == cred_ref,
            ApplicationCredential.user_id == u.id,
        )
    )
    if not ac:
        raise HTTPException(status_code=404, detail="application credential not found")
    return _appcred_to_dict(ac)


@router.delete("/v3/users/{user_ref}/application_credentials/{cred_ref}", status_code=204)
def delete_app_credential(user_ref: str, cred_ref: str, current: CurrentUser, db: DB):
    """Delete an application credential."""
    u = _get_user_by_ref(db, user_ref, scope_domain(current))
    if current.get("role") != "admin" and u.id != current["sub"]:
        raise HTTPException(status_code=403, detail="forbidden")
    ac = db.get(ApplicationCredential, cred_ref) or db.scalar(
        select(ApplicationCredential).where(
            ApplicationCredential.name == cred_ref,
            ApplicationCredential.user_id == u.id,
        )
    )
    if not ac:
        raise HTTPException(status_code=404, detail="application credential not found")
    db.delete(ac)
    db.commit()


@router.post("/v3/users/{user_ref}/application_credentials/{cred_ref}/rotate")
def rotate_app_credential(user_ref: str, cred_ref: str, current: CurrentUser, db: DB,
                          payload: RotateIn | None = Body(default=None)):
    """Issue a new secret for the credential. The replaced secret keeps working for `grace_seconds` (default 0)."""
    grace = payload.grace_seconds if payload else 0
    u = _get_user_by_ref(db, user_ref, scope_domain(current))
    if current.get("role") != "admin" and u.id != current["sub"]:
        raise HTTPException(status_code=403, detail="forbidden")
    ac = db.scalar(select(ApplicationCredential).where(
        ApplicationCredential.id == cred_ref, ApplicationCredential.user_id == u.id,
    )) or db.scalar(select(ApplicationCredential).where(
        ApplicationCredential.name == cred_ref, ApplicationCredential.user_id == u.id,
    ))
    if not ac:
        raise HTTPException(status_code=404, detail="application credential not found")
    secret_plain = secrets.token_urlsafe(32)
    if grace > 0:
        # replaces any earlier window: only the secret that was current right now survives
        ac.previous_secret_hash = ac.secret_hash
        ac.previous_valid_until = utcnow() + timedelta(seconds=grace)
    else:
        ac.previous_secret_hash = None
        ac.previous_valid_until = None
    ac.secret_hash = _hash_password(secret_plain)
    db.commit()
    db.refresh(ac)
    return _appcred_to_dict(ac, secret=secret_plain)


@router.post("/auth/tokens/application-credential", status_code=201)
def login_with_app_credential(payload: AppCredTokenIn, db: DB):
    """
    Authenticate using an Application Credential (name or ID + secret).
    Returns a scoped JWT token limited to the credential's roles.
    """
    # Find the credential by id or name
    ac: ApplicationCredential | None = None
    if payload.application_credential_id:
        ac = db.get(ApplicationCredential, payload.application_credential_id)
    elif payload.application_credential_name:
        ac = db.scalar(
            select(ApplicationCredential).where(
                ApplicationCredential.name == payload.application_credential_name
            )
        )
    if not ac or not ac.enabled:
        raise HTTPException(status_code=401, detail="invalid application credential")
    matched = _verify_password(payload.application_credential_secret, ac.secret_hash)
    if not matched and ac.previous_secret_hash and ac.previous_valid_until and ac.previous_valid_until > utcnow():
        matched = _verify_password(payload.application_credential_secret, ac.previous_secret_hash)
    if not matched:
        raise HTTPException(status_code=401, detail="invalid application credential secret")
    if ac.expires_at and ac.expires_at < utcnow():
        raise HTTPException(status_code=401, detail="application credential has expired")
    user = db.get(User, ac.user_id)
    if not user or not user.enabled:
        raise HTTPException(status_code=403, detail="user disabled")
    # the credential never grants more than its user holds right now (older rows may carry roles that were never checked)
    roles = [r for r in (ac.roles.split(",") if ac.roles else ["member"]) if role_held(db, user, user.project_id, r)]
    if not roles:
        raise HTTPException(status_code=401, detail="the application credential has no role its user still holds")
    token = _create_token(user, scope_type="project", extra_roles=roles)
    return {"token": token, "expires_in": TOKEN_EXPIRE_H * 3600, "method": "application_credential"}
