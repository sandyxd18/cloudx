# Auth service – Keystone-like identity, authentication, and access control for cloudx.
#
# Provides:
#   • Projects (tenants)
#   • Users with bcrypt passwords and roles (admin / member)
#   • Groups and group membership
#   • JWT tokens (HS256, 24 h by default)
#   • AWS-style Access Keys  (CLDX… prefix)
#
# Other services validate tokens by calling GET /auth/tokens/validate or by
# verifying the JWT signature locally using the shared AUTH_SECRET_KEY.

import os
import re
import secrets
import string
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from jose import JWTError, jwt
import bcrypt
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy import (
    Boolean, DateTime, ForeignKey, String, Text,
    create_engine, select, UniqueConstraint,
)
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, Session,
    mapped_column, sessionmaker,
)

# ── Config ─────────────────────────────────────────────────────────────────────
DATABASE_URL   = os.getenv("DATABASE_URL",   "postgresql+psycopg://mini:mini@postgres:5432/mini_openstack")
SECRET_KEY     = os.getenv("AUTH_SECRET_KEY", "cloudx-secret-key-change-in-production-min32chars!")
ALGORITHM      = "HS256"
TOKEN_EXPIRE_H = int(os.getenv("AUTH_TOKEN_EXPIRE_HOURS", "24"))
ADMIN_USERNAME = os.getenv("AUTH_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("AUTH_ADMIN_PASSWORD", "admin123")
ADMIN_EMAIL    = os.getenv("AUTH_ADMIN_EMAIL",    "admin@cloudx.local")
ADMIN_PROJECT  = os.getenv("AUTH_ADMIN_PROJECT",  "admin")

engine       = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


# ── ORM ────────────────────────────────────────────────────────────────────────
class Base(DeclarativeBase):
    pass


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Project(Base):
    __tablename__ = "auth_projects"
    id:          Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name:        Mapped[str] = mapped_column(String, unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled:     Mapped[bool] = mapped_column(Boolean, default=True)
    created_at:  Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class User(Base):
    __tablename__ = "auth_users"
    id:            Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    username:      Mapped[str] = mapped_column(String, unique=True, index=True)
    email:         Mapped[str] = mapped_column(String, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String)
    project_id:    Mapped[str] = mapped_column(String, ForeignKey("auth_projects.id"))
    role:          Mapped[str] = mapped_column(String, default="member")   # admin | member
    enabled:       Mapped[bool] = mapped_column(Boolean, default=True)
    created_at:    Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Group(Base):
    __tablename__ = "auth_groups"
    id:          Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name:        Mapped[str] = mapped_column(String, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    project_id:  Mapped[str] = mapped_column(String, ForeignKey("auth_projects.id"))
    created_at:  Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    __table_args__ = (UniqueConstraint("name", "project_id"),)


class GroupMember(Base):
    __tablename__ = "auth_group_members"
    group_id: Mapped[str] = mapped_column(String, ForeignKey("auth_groups.id"), primary_key=True)
    user_id:  Mapped[str] = mapped_column(String, ForeignKey("auth_users.id"),  primary_key=True)


class RevokedToken(Base):
    """Store revoked JTIs so logout works before expiry."""
    __tablename__ = "auth_revoked_tokens"
    jti:        Mapped[str] = mapped_column(String, primary_key=True)
    revoked_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class AccessKey(Base):
    __tablename__ = "auth_access_keys"
    id:              Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    access_key_id:   Mapped[str] = mapped_column(String, unique=True, index=True)
    secret_key_hash: Mapped[str] = mapped_column(String)
    user_id:         Mapped[str] = mapped_column(String, ForeignKey("auth_users.id"))
    project_id:      Mapped[str] = mapped_column(String, ForeignKey("auth_projects.id"))
    name:            Mapped[str] = mapped_column(String, default="default")
    enabled:         Mapped[bool] = mapped_column(Boolean, default=True)
    created_at:      Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# ── Helpers ────────────────────────────────────────────────────────────────────
def _gen_access_key_id() -> str:
    """Generate CLDX-prefixed 20-char access key ID."""
    charset = string.ascii_uppercase + string.digits
    suffix  = "".join(secrets.choice(charset) for _ in range(16))
    return f"CLDX{suffix}"


def _gen_secret_key() -> str:
    """Generate 40-char URL-safe secret access key."""
    return secrets.token_urlsafe(30)


def _hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def _verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


def _create_token(user: User) -> str:
    jti = str(uuid.uuid4())
    payload = {
        "jti":        jti,
        "sub":        user.id,
        "username":   user.username,
        "project_id": user.project_id,
        "role":       user.role,
        "exp":        utcnow() + timedelta(hours=TOKEN_EXPIRE_H),
        "iat":        utcnow(),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def _decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError as e:
        raise HTTPException(status_code=401, detail=f"invalid token: {e}")


def _get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


DB = Annotated[Session, Depends(_get_db)]


def _require_token(request: Request, db: DB) -> dict:
    """Dependency: extract + validate bearer token from Authorization header."""
    auth = request.headers.get("Authorization", "") or request.headers.get("X-Auth-Token", "")
    token = auth.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="authentication required")
    payload = _decode_token(token)
    jti = payload.get("jti", "")
    revoked = db.get(RevokedToken, jti)
    if revoked:
        raise HTTPException(status_code=401, detail="token has been revoked")
    return payload


def _require_admin(payload: dict = Depends(_require_token)) -> dict:
    if payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="admin role required")
    return payload


CurrentUser = Annotated[dict, Depends(_require_token)]
AdminUser   = Annotated[dict, Depends(_require_admin)]


def _get_user_by_ref(db: Session, ref: str) -> User:
    u = db.get(User, ref) or db.scalar(select(User).where(User.username == ref))
    if not u:
        raise HTTPException(status_code=404, detail="user not found")
    return u


def _get_project_by_ref(db: Session, ref: str) -> Project:
    p = db.get(Project, ref) or db.scalar(select(Project).where(Project.name == ref))
    if not p:
        raise HTTPException(status_code=404, detail="project not found")
    return p


def _get_group_by_ref(db: Session, ref: str, project_id: str | None = None) -> Group:
    g = db.get(Group, ref)
    if not g:
        q = select(Group).where(Group.name == ref)
        if project_id:
            q = q.where(Group.project_id == project_id)
        g = db.scalar(q)
    if not g:
        raise HTTPException(status_code=404, detail="group not found")
    return g


def _project_to_dict(p: Project) -> dict:
    return {
        "id": p.id, "name": p.name, "description": p.description,
        "enabled": p.enabled, "created_at": p.created_at.isoformat(),
    }


def _user_to_dict(u: User) -> dict:
    return {
        "id": u.id, "username": u.username, "email": u.email,
        "project_id": u.project_id, "role": u.role,
        "enabled": u.enabled, "created_at": u.created_at.isoformat(),
    }


def _group_to_dict(g: Group, members: list[str] = []) -> dict:
    return {
        "id": g.id, "name": g.name, "description": g.description,
        "project_id": g.project_id, "members": members,
        "created_at": g.created_at.isoformat(),
    }


def _key_to_dict(k: AccessKey, secret: str | None = None) -> dict:
    d = {
        "id": k.id, "access_key_id": k.access_key_id,
        "name": k.name, "user_id": k.user_id,
        "project_id": k.project_id, "enabled": k.enabled,
        "created_at": k.created_at.isoformat(),
    }
    if secret:
        d["secret_access_key"] = secret
    return d


# ── Pydantic schemas ───────────────────────────────────────────────────────────
class ProjectIn(BaseModel):
    name: str
    description: str | None = None
    enabled: bool = True


class ProjectUpdate(BaseModel):
    description: str | None = None
    enabled: bool | None = None


class UserIn(BaseModel):
    username: str
    email: str
    password: str
    project_id: str  # project name or id
    role: str = "member"

    @field_validator("role")
    @classmethod
    def _valid_role(cls, v: str) -> str:
        if v not in ("admin", "member"):
            raise ValueError("role must be 'admin' or 'member'")
        return v


class UserUpdate(BaseModel):
    email:    str | None = None
    password: str | None = None
    role:     str | None = None
    enabled:  bool | None = None


class GroupIn(BaseModel):
    name:        str
    description: str | None = None


class LoginIn(BaseModel):
    username: str
    password: str


class AccessKeyIn(BaseModel):
    name: str = "default"


# ── Bootstrap ──────────────────────────────────────────────────────────────────
def _bootstrap():
    db = SessionLocal()
    try:
        Base.metadata.create_all(bind=engine)
        # Create admin project
        proj = db.scalar(select(Project).where(Project.name == ADMIN_PROJECT))
        if not proj:
            proj = Project(name=ADMIN_PROJECT, description="Default admin project")
            db.add(proj)
            db.commit()
            db.refresh(proj)
        # Create admin user
        user = db.scalar(select(User).where(User.username == ADMIN_USERNAME))
        if not user:
            user = User(
                username=ADMIN_USERNAME,
                email=ADMIN_EMAIL,
                password_hash=_hash_password(ADMIN_PASSWORD),
                project_id=proj.id,
                role="admin",
            )
            db.add(user)
            db.commit()
            print(f"[auth] bootstrap: admin user '{ADMIN_USERNAME}' created in project '{ADMIN_PROJECT}'", flush=True)
        else:
            print(f"[auth] bootstrap: admin user '{ADMIN_USERNAME}' already exists", flush=True)
    finally:
        db.close()


# ── App ────────────────────────────────────────────────────────────────────────
app = FastAPI(title="cloudx auth-api", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    _bootstrap()


@app.get("/health")
def health():
    return {"status": "ok", "service": "auth-api"}


# ── Auth / Tokens ──────────────────────────────────────────────────────────────
@app.post("/auth/tokens", status_code=201)
def login(payload: LoginIn, db: DB):
    """Authenticate with username + password, return JWT token."""
    user = db.scalar(select(User).where(User.username == payload.username))
    if not user or not _verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="invalid credentials")
    if not user.enabled:
        raise HTTPException(status_code=403, detail="user disabled")
    proj = db.get(Project, user.project_id)
    if not proj or not proj.enabled:
        raise HTTPException(status_code=403, detail="project disabled")
    token = _create_token(user)
    return {
        "token": token,
        "expires_in": TOKEN_EXPIRE_H * 3600,
        "user":    _user_to_dict(user),
        "project": _project_to_dict(proj),
    }


@app.post("/auth/tokens/access-key", status_code=201)
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
    token = _create_token(user)
    return {"token": token, "expires_in": TOKEN_EXPIRE_H * 3600}


@app.get("/auth/tokens/validate")
def validate_token(current: CurrentUser):
    """Internal: validate a token and return its claims."""
    return current


@app.delete("/auth/tokens/current", status_code=204)
def logout(current: CurrentUser, db: DB):
    """Revoke the current token (logout)."""
    jti = current.get("jti", "")
    exp_ts = current.get("exp", 0)
    exp_dt = datetime.fromtimestamp(exp_ts, tz=timezone.utc).replace(tzinfo=None)
    if jti:
        db.merge(RevokedToken(jti=jti, expires_at=exp_dt))
        db.commit()


# ── Projects ───────────────────────────────────────────────────────────────────
@app.post("/projects", status_code=201)
def create_project(payload: ProjectIn, _: AdminUser, db: DB):
    if db.scalar(select(Project).where(Project.name == payload.name)):
        raise HTTPException(status_code=409, detail="project already exists")
    p = Project(**payload.model_dump())
    db.add(p)
    db.commit()
    db.refresh(p)
    return _project_to_dict(p)


@app.get("/projects")
def list_projects(_: CurrentUser, db: DB):
    return {"items": [_project_to_dict(p) for p in db.scalars(select(Project)).all()]}


@app.get("/projects/{ref}")
def show_project(ref: str, _: CurrentUser, db: DB):
    return _project_to_dict(_get_project_by_ref(db, ref))


@app.put("/projects/{ref}")
def update_project(ref: str, payload: ProjectUpdate, _: AdminUser, db: DB):
    p = _get_project_by_ref(db, ref)
    for k, v in payload.model_dump(exclude_none=True).items():
        setattr(p, k, v)
    db.commit()
    db.refresh(p)
    return _project_to_dict(p)


@app.delete("/projects/{ref}", status_code=204)
def delete_project(ref: str, _: AdminUser, db: DB):
    p = _get_project_by_ref(db, ref)
    if p.name == ADMIN_PROJECT:
        raise HTTPException(status_code=400, detail="cannot delete admin project")
    db.delete(p)
    db.commit()


# ── Users ──────────────────────────────────────────────────────────────────────
@app.post("/users", status_code=201)
def create_user(payload: UserIn, _: AdminUser, db: DB):
    if db.scalar(select(User).where(User.username == payload.username)):
        raise HTTPException(status_code=409, detail="username already exists")
    if db.scalar(select(User).where(User.email == payload.email)):
        raise HTTPException(status_code=409, detail="email already exists")
    proj = _get_project_by_ref(db, payload.project_id)
    u = User(
        username=payload.username,
        email=payload.email,
        password_hash=_hash_password(payload.password),
        project_id=proj.id,
        role=payload.role,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return _user_to_dict(u)


@app.get("/users")
def list_users(_: AdminUser, db: DB):
    return {"items": [_user_to_dict(u) for u in db.scalars(select(User)).all()]}


@app.get("/users/{ref}")
def show_user(ref: str, current: CurrentUser, db: DB):
    u = _get_user_by_ref(db, ref)
    # Non-admins can only see themselves
    if current.get("role") != "admin" and u.id != current.get("sub"):
        raise HTTPException(status_code=403, detail="forbidden")
    return _user_to_dict(u)


@app.put("/users/{ref}")
def update_user(ref: str, payload: UserUpdate, current: CurrentUser, db: DB):
    u = _get_user_by_ref(db, ref)
    if current.get("role") != "admin" and u.id != current.get("sub"):
        raise HTTPException(status_code=403, detail="forbidden")
    if payload.role and current.get("role") != "admin":
        raise HTTPException(status_code=403, detail="only admin can change roles")
    data = payload.model_dump(exclude_none=True)
    if "password" in data:
        u.password_hash = _hash_password(data.pop("password"))
    for k, v in data.items():
        setattr(u, k, v)
    db.commit()
    db.refresh(u)
    return _user_to_dict(u)


@app.delete("/users/{ref}", status_code=204)
def delete_user(ref: str, _: AdminUser, db: DB):
    u = _get_user_by_ref(db, ref)
    if u.username == ADMIN_USERNAME:
        raise HTTPException(status_code=400, detail="cannot delete admin user")
    db.delete(u)
    db.commit()


# ── Groups ─────────────────────────────────────────────────────────────────────
@app.post("/groups", status_code=201)
def create_group(payload: GroupIn, current: CurrentUser, db: DB):
    proj_id = current["project_id"]
    if db.scalar(select(Group).where(Group.name == payload.name, Group.project_id == proj_id)):
        raise HTTPException(status_code=409, detail="group already exists in project")
    g = Group(project_id=proj_id, **payload.model_dump())
    db.add(g)
    db.commit()
    db.refresh(g)
    return _group_to_dict(g)


@app.get("/groups")
def list_groups(current: CurrentUser, db: DB):
    proj_id = current["project_id"]
    if current.get("role") == "admin":
        groups = db.scalars(select(Group)).all()
    else:
        groups = db.scalars(select(Group).where(Group.project_id == proj_id)).all()
    result = []
    for g in groups:
        members = [m.user_id for m in db.scalars(select(GroupMember).where(GroupMember.group_id == g.id)).all()]
        result.append(_group_to_dict(g, members))
    return {"items": result}


@app.put("/groups/{ref}/members/{user_ref}")
def add_group_member(ref: str, user_ref: str, current: CurrentUser, db: DB):
    g = _get_group_by_ref(db, ref, current["project_id"])
    u = _get_user_by_ref(db, user_ref)
    exists = db.scalar(select(GroupMember).where(
        GroupMember.group_id == g.id, GroupMember.user_id == u.id))
    if not exists:
        db.add(GroupMember(group_id=g.id, user_id=u.id))
        db.commit()
    return {"group_id": g.id, "user_id": u.id, "status": "member"}


@app.delete("/groups/{ref}/members/{user_ref}", status_code=204)
def remove_group_member(ref: str, user_ref: str, current: CurrentUser, db: DB):
    g = _get_group_by_ref(db, ref, current["project_id"])
    u = _get_user_by_ref(db, user_ref)
    m = db.scalar(select(GroupMember).where(
        GroupMember.group_id == g.id, GroupMember.user_id == u.id))
    if m:
        db.delete(m)
        db.commit()


@app.delete("/groups/{ref}", status_code=204)
def delete_group(ref: str, current: CurrentUser, db: DB):
    g = _get_group_by_ref(db, ref, current["project_id"])
    db.delete(g)
    db.commit()


# ── Access Keys ────────────────────────────────────────────────────────────────
@app.post("/access-keys", status_code=201)
def create_access_key(payload: AccessKeyIn, current: CurrentUser, db: DB):
    """Generate a new CLDX access key for the current user."""
    access_key_id = _gen_access_key_id()
    secret        = _gen_secret_key()
    ak = AccessKey(
        access_key_id=access_key_id,
        secret_key_hash=_hash_password(secret),
        user_id=current["sub"],
        project_id=current["project_id"],
        name=payload.name,
    )
    db.add(ak)
    db.commit()
    db.refresh(ak)
    # Return the raw secret ONCE — it cannot be retrieved again
    return _key_to_dict(ak, secret=secret)


@app.get("/access-keys")
def list_access_keys(current: CurrentUser, db: DB):
    uid = current["sub"]
    if current.get("role") == "admin":
        keys = db.scalars(select(AccessKey)).all()
    else:
        keys = db.scalars(select(AccessKey).where(AccessKey.user_id == uid)).all()
    return {"items": [_key_to_dict(k) for k in keys]}


@app.delete("/access-keys/{key_id}", status_code=204)
def revoke_access_key(key_id: str, current: CurrentUser, db: DB):
    ak = db.get(AccessKey, key_id) or db.scalar(
        select(AccessKey).where(AccessKey.access_key_id == key_id))
    if not ak:
        raise HTTPException(status_code=404, detail="access key not found")
    if current.get("role") != "admin" and ak.user_id != current["sub"]:
        raise HTTPException(status_code=403, detail="forbidden")
    db.delete(ak)
    db.commit()
