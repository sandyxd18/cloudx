from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from ..config import TOKEN_EXPIRE_H
from ..db import DB
from ..deps import CurrentUser
from ..models import AccessKey, Domain, Group, GroupMember, Project, RevokedToken, RoleAssignment, User
from ..schemas import LoginIn, SwitchProjectIn
from ..serializers import _project_to_dict, _user_to_dict
from ..tokens import _build_catalog, _create_token, _verify_password, project_role

router = APIRouter()


def _member_projects(db, user: User) -> set[str]:
    """Home project + projects of the user's groups + projects with an explicit role assignment."""
    ids = {user.project_id} if user.project_id else set()
    ids.update(db.scalars(
        select(Group.project_id).join(GroupMember, Group.id == GroupMember.group_id).where(GroupMember.user_id == user.id)
    ).all())
    ids.update(db.scalars(select(RoleAssignment.project_id).where(RoleAssignment.user_id == user.id)).all())
    return ids  # cross-domain projects are refused by _check_domain

def _check_domain(db, user: User, proj: Project | None = None) -> None:
    d = db.get(Domain, user.domain_id)
    if d and not d.enabled:
        raise HTTPException(status_code=403, detail="domain disabled")
    if proj is not None and user.role != "admin" and proj.domain_id != user.domain_id:
        raise HTTPException(status_code=403, detail="project belongs to another domain")


# ── Auth / Tokens ──────────────────────────────────────────────────────────────
@router.post("/auth/tokens", status_code=201)
def login(payload: LoginIn, db: DB):
    """Authenticate with username + password, return JWT token."""
    q = select(User).where(User.username == payload.username)
    if payload.domain:
        dom = db.get(Domain, payload.domain) or db.scalar(select(Domain).where(Domain.name == payload.domain))
        q = q.where(User.domain_id == (dom.id if dom else payload.domain))
    # usernames are unique per domain: the password decides between same-named users
    ok = [u for u in db.scalars(q.limit(5)).all() if _verify_password(payload.password, u.password_hash)]
    if len(ok) > 1:
        raise HTTPException(status_code=409, detail="username exists in several domains; pass domain")
    user = ok[0] if ok else None
    if not user:
        raise HTTPException(status_code=401, detail="invalid credentials")
    if not user.enabled:
        raise HTTPException(status_code=403, detail="user disabled")
    _check_domain(db, user)

    target_project_id = user.project_id
    if payload.project:
        req_proj = db.get(Project, payload.project) or db.scalar(
            select(Project).where(Project.name == payload.project).order_by(Project.domain_id != user.domain_id).limit(1)
        )  # own domain first, so a cross-domain name still gives the 403 from _check_domain
        if not req_proj or not req_proj.enabled:
            raise HTTPException(status_code=404, detail="project not found or disabled")
        _check_domain(db, user, req_proj)
        if user.role != "admin" and user.project_id != req_proj.id:
            if req_proj.id not in _member_projects(db, user):
                raise HTTPException(status_code=403, detail="you are not a member of that project")
        target_project_id = req_proj.id

    proj = db.get(Project, target_project_id)
    if not proj or not proj.enabled:
        raise HTTPException(status_code=403, detail="project disabled")

    orig_project = user.project_id
    user.project_id = target_project_id
    token = _create_token(user, project_name=proj.name, role=project_role(db, user, proj.id))
    user.project_id = orig_project

    return {
        "token": token,
        "expires_in": TOKEN_EXPIRE_H * 3600,
        "user":    _user_to_dict(user),
        "project": _project_to_dict(proj),
        "catalog": _build_catalog(db),
    }


@router.post("/auth/tokens/access-key", status_code=201)
def login_with_access_key(
    request: Request,
    db: DB,
):
    """Authenticate with access_key_id + secret_access_key, return JWT token."""
    key_id  = request.headers.get("X-Access-Key-Id", "")
    secret  = request.headers.get("X-Secret-Access-Key", "")
    if not key_id or not secret:
        raise HTTPException(status_code=400, detail="X-Access-Key-Id and X-Secret-Access-Key headers required")
    ak = db.scalar(select(AccessKey).where(AccessKey.access_key_id == key_id))
    if not ak or not ak.enabled:
        raise HTTPException(status_code=401, detail="invalid access key")
    if not _verify_password(secret, ak.secret_key_hash):
        raise HTTPException(status_code=401, detail="invalid credentials")
    user = db.get(User, ak.user_id)
    if not user or not user.enabled:
        raise HTTPException(status_code=403, detail="user disabled")
    proj = db.get(Project, ak.project_id)
    if not proj or not proj.enabled:
        raise HTTPException(status_code=403, detail="project disabled")
    _check_domain(db, user, proj)
    orig_project = user.project_id
    user.project_id = proj.id
    token = _create_token(user, project_name=proj.name, role=project_role(db, user, proj.id))
    user.project_id = orig_project
    return {"token": token, "expires_in": TOKEN_EXPIRE_H * 3600}


@router.get("/auth/tokens/validate")
def validate_token(current: CurrentUser):
    """Internal: validate a token and return its claims."""
    return current


@router.delete("/auth/tokens/current", status_code=204)
def logout(current: CurrentUser, db: DB):
    """Revoke the current token (logout)."""
    jti = current.get("jti", "")
    exp_ts = current.get("exp", 0)
    exp_dt = datetime.fromtimestamp(exp_ts, tz=timezone.utc).replace(tzinfo=None)
    if jti:
        db.merge(RevokedToken(jti=jti, expires_at=exp_dt))
        db.commit()


@router.post("/session/switch-project")
def switch_project(payload: SwitchProjectIn, current: CurrentUser, db: DB):
    """Re-issue a token for a different project (user must be a member or admin)."""
    user = db.get(User, current["sub"])
    if not user:
        raise HTTPException(status_code=404, detail="user not found")
    proj = db.get(Project, payload.project_id) or db.scalar(
        select(Project).where(Project.name == payload.project_id).order_by(Project.domain_id != user.domain_id).limit(1)
    )
    if not proj or not proj.enabled:
        raise HTTPException(status_code=404, detail="project not found or disabled")
    _check_domain(db, user, proj)
    # Admin can switch to any project; regular users can only switch to their assigned projects
    if current.get("role") != "admin":
        if proj.id not in _member_projects(db, user):
            raise HTTPException(status_code=403, detail="you are not a member of that project")
    # Temporarily set the user's active project for token generation
    orig_project = user.project_id
    user.project_id = proj.id
    token = _create_token(user, project_name=proj.name, role=project_role(db, user, proj.id))
    user.project_id = orig_project  # don't persist the change
    return {
        "token": token,
        "expires_in": TOKEN_EXPIRE_H * 3600,
        "user": _user_to_dict(user),
        "project": _project_to_dict(proj),
    }
