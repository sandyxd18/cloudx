import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
try:
    from shared.config import load_service_config
    load_service_config("auth-api")
except Exception as _cfg_err:
    pass

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
import sys
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from jose import JWTError, jwt
import bcrypt
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Integer, String, Text,
    create_engine, select, UniqueConstraint, text, inspect,
)
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, Session,
    mapped_column, sessionmaker,
)

# Load shared modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
try:
    from shared.request_id_middleware import RequestIDMiddleware
except ImportError:
    RequestIDMiddleware = None

# Load shared policy engine
try:
    from shared.policy import enforce as policy_enforce, list_rules as policy_list_rules
except ImportError:
    def policy_enforce(*args, **kwargs): return True
    def policy_list_rules(): return {}

# ── Config ─────────────────────────────────────────────────────────────────────
DATABASE_URL   = os.getenv("DATABASE_URL",   "postgresql+psycopg://mini:mini@postgres:5432/mini_openstack")
SECRET_KEY     = os.getenv("AUTH_SECRET_KEY", "cloudx-secret-key-change-in-production-min32chars!")
ALGORITHM      = "HS256"
TOKEN_EXPIRE_H = int(os.getenv("AUTH_TOKEN_EXPIRE_HOURS", "24"))
ADMIN_USERNAME = os.getenv("AUTH_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("AUTH_ADMIN_PASSWORD", "admin123")
ADMIN_EMAIL    = os.getenv("AUTH_ADMIN_EMAIL",    "admin@cloudx.local")
ADMIN_PROJECT  = os.getenv("AUTH_ADMIN_PROJECT",  "admin")

# Service URLs for service catalog bootstrap
_CLOUDX_SERVICES = [
    {"name": "identity",     "type": "identity",     "url": os.getenv("AUTH_API_URL",        "http://auth-api:8000")},
    {"name": "compute",      "type": "compute",      "url": os.getenv("COMPUTE_API_URL",    "http://compute-api:8000")},
    {"name": "image",        "type": "image",        "url": os.getenv("IMAGE_API_URL",      "http://image-api:8000")},
    {"name": "network",      "type": "network",      "url": os.getenv("NETWORKING_API_URL", "http://networking-api:8000")},
    {"name": "volume",       "type": "block-storage","url": os.getenv("BLOCK_STORE_API_URL","http://block-store-api:8000")},
    {"name": "placement",    "type": "placement",    "url": os.getenv("PLACEMENT_API_URL",  "http://placement-api:8000")},
    {"name": "object-store", "type": "object-store", "url": os.getenv("OBJECT_STORE_API_URL","http://object-store-api:8000")},
    {"name": "load-balancer","type": "load-balancer","url": os.getenv("LOAD_BALANCER_API_URL","http://load-balancer-api:8000")},
    {"name": "metadata",     "type": "metadata",     "url": os.getenv("METADATA_API_URL",   "http://metadata-api:8775")},
    {"name": "telemetry",    "type": "telemetry",    "url": os.getenv("TELEMETRY_API_URL",  "http://telemetry-api:8010")},
    {"name": "billing",      "type": "billing",      "url": os.getenv("BILLING_API_URL",    "http://billing-api:8011")},
    {"name": "event",        "type": "event",        "url": os.getenv("EVENT_API_URL",      "http://event-api:8012")},
    {"name": "orchestration","type": "orchestration","url": os.getenv("ORCHESTRATION_API_URL","http://orchestration-api:8013")},
]

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


class ProjectQuota(Base):
    """Quota limits per project."""
    __tablename__ = "auth_project_quotas"
    project_id:    Mapped[str] = mapped_column(String, ForeignKey("auth_projects.id", ondelete="CASCADE"), primary_key=True)
    max_instances: Mapped[int] = mapped_column(Integer, default=10)
    max_vcpus:     Mapped[int] = mapped_column(Integer, default=20)
    max_ram_mb:    Mapped[int] = mapped_column(Integer, default=32768)
    max_volume_gb: Mapped[int] = mapped_column(Integer, default=100)
    updated_at:    Mapped[datetime] = mapped_column(DateTime, default=utcnow)


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


class ServiceCatalogEntry(Base):
    """Registered service (e.g. compute, image, network)."""
    __tablename__ = "auth_services"
    id:           Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name:         Mapped[str] = mapped_column(String, unique=True, index=True)
    type:         Mapped[str] = mapped_column(String)           # compute | image | network | …
    description:  Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled:      Mapped[bool] = mapped_column(Boolean, default=True)
    created_at:   Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ServiceEndpoint(Base):
    """Endpoint URL for a registered service."""
    __tablename__ = "auth_service_endpoints"
    id:           Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    service_id:   Mapped[str] = mapped_column(String, ForeignKey("auth_services.id", ondelete="CASCADE"))
    interface:    Mapped[str] = mapped_column(String, default="public")  # public | internal | admin
    region:       Mapped[str] = mapped_column(String, default="RegionOne")
    url:          Mapped[str] = mapped_column(String)
    enabled:      Mapped[bool] = mapped_column(Boolean, default=True)
    created_at:   Mapped[datetime] = mapped_column(DateTime, default=utcnow)


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


class Domain(Base):
    """Keystone v3 Domain (top-level organizational unit)."""
    __tablename__ = "auth_domains"
    id:          Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name:        Mapped[str] = mapped_column(String, unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled:     Mapped[bool] = mapped_column(Boolean, default=True)
    created_at:  Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ApplicationCredential(Base):
    """Keystone Application Credentials — pre-scoped tokens for automation/CI."""
    __tablename__ = "auth_application_credentials"
    id:          Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name:        Mapped[str] = mapped_column(String, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_id:     Mapped[str] = mapped_column(String, ForeignKey("auth_users.id"))
    project_id:  Mapped[str] = mapped_column(String, ForeignKey("auth_projects.id"))
    # Roles that this credential can exercise (comma-separated list, e.g. "member,reader")
    roles:       Mapped[str] = mapped_column(String, default="member")
    secret_hash: Mapped[str] = mapped_column(String)     # bcrypt of the plaintext secret
    expires_at:  Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    enabled:     Mapped[bool] = mapped_column(Boolean, default=True)
    created_at:  Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    __table_args__ = (UniqueConstraint("name", "user_id"),)


class AuditLog(Base):
    """Centralized Audit Logging for resource mutations."""
    __tablename__ = "auth_audit_logs"
    id:            Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    user_id:       Mapped[str | None] = mapped_column(String, nullable=True)
    username:      Mapped[str | None] = mapped_column(String, nullable=True)
    project_id:    Mapped[str | None] = mapped_column(String, nullable=True)
    action:        Mapped[str] = mapped_column(String, index=True)
    resource_type: Mapped[str] = mapped_column(String, index=True)
    resource_id:   Mapped[str | None] = mapped_column(String, nullable=True)
    details:       Mapped[str | None] = mapped_column(Text, nullable=True)
    timestamp:     Mapped[datetime] = mapped_column(DateTime, default=utcnow)


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


def _create_token(user: User, scope_type: str = "project", extra_roles: list[str] | None = None) -> str:
    """Create a JWT token.

    Args:
        user: the authenticated user.
        scope_type: 'project' (default) | 'domain' | 'system'.
        extra_roles: additional roles from an Application Credential (overrides user.role).
    """
    jti = str(uuid.uuid4())
    payload = {
        "jti":        jti,
        "sub":        user.id,
        "username":   user.username,
        "project_id": user.project_id,
        "role":       (extra_roles[0] if extra_roles else user.role),
        "roles":      (extra_roles if extra_roles else [user.role]),
        "scope_type": scope_type,   # Fase 4: multi-scope token support
        "exp":        utcnow() + timedelta(hours=TOKEN_EXPIRE_H),
        "iat":        utcnow(),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def _build_catalog(db: Session) -> list:
    """Build OpenStack-compatible service catalog from DB."""
    catalog = []
    services = db.scalars(select(ServiceCatalogEntry).where(ServiceCatalogEntry.enabled == True)).all()
    for svc in services:
        endpoints = db.scalars(
            select(ServiceEndpoint).where(
                ServiceEndpoint.service_id == svc.id,
                ServiceEndpoint.enabled == True,
            )
        ).all()
        catalog.append({
            "id":        svc.id,
            "name":      svc.name,
            "type":      svc.type,
            "endpoints": [
                {"id": ep.id, "interface": ep.interface, "region": ep.region, "url": ep.url}
                for ep in endpoints
            ],
        })
    return catalog


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


class QuotaUpdate(BaseModel):
    max_instances: int | None = Field(default=None, ge=0)
    max_vcpus: int | None = Field(default=None, ge=0)
    max_ram_mb: int | None = Field(default=None, ge=0)
    max_volume_gb: int | None = Field(default=None, ge=0)


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


# ── Application Credential Schemas ────────────────────────────────────────────
class AppCredIn(BaseModel):
    name: str
    description: str | None = None
    roles: list[str] = ["member"]
    expires_at: str | None = None  # ISO 8601 datetime string, optional


class AppCredTokenIn(BaseModel):
    """Auth request using an application credential (name+secret pair)."""
    application_credential_id: str | None = None
    application_credential_name: str | None = None
    application_credential_secret: str


# ── Bootstrap ──────────────────────────────────────────────────────────────────
def _ensure_domain_columns():
    """Add domain_id column to auth_projects and auth_users if missing, then migrate."""
    inspector = inspect(engine)
    table_names = inspector.get_table_names()

    if "auth_projects" in table_names:
        proj_cols = {c["name"] for c in inspector.get_columns("auth_projects")}
        with engine.begin() as conn:
            if "domain_id" not in proj_cols:
                conn.execute(text("ALTER TABLE auth_projects ADD COLUMN domain_id VARCHAR"))

    if "auth_users" in table_names:
        user_cols = {c["name"] for c in inspector.get_columns("auth_users")}
        with engine.begin() as conn:
            if "domain_id" not in user_cols:
                conn.execute(text("ALTER TABLE auth_users ADD COLUMN domain_id VARCHAR"))


def _bootstrap():
    db = SessionLocal()
    try:
        Base.metadata.create_all(bind=engine)
        _ensure_domain_columns()

        # ── Ensure default domain exists ──────────────────────────────────────
        default_domain = db.scalar(select(Domain).where(Domain.name == "Default"))
        if not default_domain:
            default_domain = Domain(id="default", name="Default", description="Default domain")
            db.add(default_domain)
            db.commit()
            db.refresh(default_domain)
            print("[auth] bootstrap: default domain created", flush=True)

        # ── Migrate existing projects / users to default domain ───────────────
        with engine.begin() as conn:
            conn.execute(text(
                "UPDATE auth_projects SET domain_id = 'default' WHERE domain_id IS NULL"
            ))
            conn.execute(text(
                "UPDATE auth_users SET domain_id = 'default' WHERE domain_id IS NULL"
            ))

        # ── Create admin project ──────────────────────────────────────────────
        proj = db.scalar(select(Project).where(Project.name == ADMIN_PROJECT))
        if not proj:
            proj = Project(name=ADMIN_PROJECT, description="Default admin project")
            db.add(proj)
            db.commit()
            db.refresh(proj)
        # Ensure admin project has domain_id
        with engine.begin() as conn:
            conn.execute(text(
                "UPDATE auth_projects SET domain_id = 'default' WHERE domain_id IS NULL"
            ))

        # ── Create admin user ─────────────────────────────────────────────────
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

        # ── Create service project and system service users ───────────────────
        service_proj = db.scalar(select(Project).where(Project.name == "service"))
        if not service_proj:
            service_proj = Project(name="service", description="CloudX System Service Accounts Project")
            db.add(service_proj)
            db.commit()
            db.refresh(service_proj)

        service_accounts = [
            ("service_compute", "service_compute@cloudx.local"),
            ("service_network", "service_network@cloudx.local"),
            ("service_image", "service_image@cloudx.local"),
            ("service_block_store", "service_block_store@cloudx.local"),
            ("service_load_balancer", "service_load_balancer@cloudx.local"),
            ("service_object_store", "service_object_store@cloudx.local"),
        ]
        for svc_user, svc_email in service_accounts:
            existing_svc_user = db.scalar(select(User).where(User.username == svc_user))
            if not existing_svc_user:
                db.add(User(
                    username=svc_user,
                    email=svc_email,
                    password_hash=_hash_password(ADMIN_PASSWORD),
                    project_id=service_proj.id,
                    role="admin",
                ))
        db.commit()

        # ── Bootstrap service catalog ─────────────────────────────────────────
        _bootstrap_service_catalog(db)
    finally:
        db.close()


def _bootstrap_service_catalog(db: Session) -> None:
    """Register all CloudX services into the service catalog if not already present."""
    for svc_def in _CLOUDX_SERVICES:
        existing = db.scalar(select(ServiceCatalogEntry).where(ServiceCatalogEntry.name == svc_def["name"]))
        if not existing:
            svc = ServiceCatalogEntry(
                name=svc_def["name"],
                type=svc_def["type"],
                description=f"CloudX {svc_def['name']} service",
            )
            db.add(svc)
            db.flush()  # get svc.id without full commit
            # Add public + internal endpoints
            for iface in ("public", "internal"):
                db.add(ServiceEndpoint(
                    service_id=svc.id,
                    interface=iface,
                    region="RegionOne",
                    url=svc_def["url"],
                ))
            db.commit()
            print(f"[auth] bootstrap: service catalog entry '{svc_def['name']}' registered", flush=True)
        else:
            # Ensure endpoint URLs are up to date
            endpoints = db.scalars(
                select(ServiceEndpoint).where(ServiceEndpoint.service_id == existing.id)
            ).all()
            if not endpoints:
                for iface in ("public", "internal"):
                    db.add(ServiceEndpoint(
                        service_id=existing.id,
                        interface=iface,
                        region="RegionOne",
                        url=svc_def["url"],
                    ))
                db.commit()


# ── App ────────────────────────────────────────────────────────────────────────
app = FastAPI(title="cloudx auth-api", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
if RequestIDMiddleware:
    app.add_middleware(RequestIDMiddleware)

# Prometheus metrics (/metrics) — optional, no-op if prometheus_client missing
try:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from shared.metrics import setup_metrics
    setup_metrics(app, "auth-api")
except Exception:
    pass


@app.on_event("startup")
def startup():
    _bootstrap()


@app.get("/health")
def health():
    return {"status": "ok", "service": "auth-api"}


@app.get("/healthcheck")
def healthcheck(db: DB):
    """Standard CloudX health check endpoint."""
    checks = {}
    overall = "up"
    try:
        db.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as exc:
        checks["db"] = f"error: {exc}"
        overall = "degraded"
    return {"status": overall, "checks": checks}



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
        "catalog": _build_catalog(db),
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


class SwitchProjectIn(BaseModel):
    project_id: str


@app.post("/session/switch-project")
def switch_project(payload: SwitchProjectIn, current: CurrentUser, db: DB):
    """Re-issue a token for a different project (user must be a member or admin)."""
    user = db.get(User, current["sub"])
    if not user:
        raise HTTPException(status_code=404, detail="user not found")
    proj = db.get(Project, payload.project_id) or db.scalar(
        select(Project).where(Project.name == payload.project_id)
    )
    if not proj or not proj.enabled:
        raise HTTPException(status_code=404, detail="project not found or disabled")
    # Admin can switch to any project; regular users can only switch to their own project
    if current.get("role") != "admin" and user.project_id != proj.id:
        raise HTTPException(status_code=403, detail="you are not a member of that project")
    # Temporarily set the user's active project for token generation
    orig_project = user.project_id
    user.project_id = proj.id
    token = _create_token(user)
    user.project_id = orig_project  # don't persist the change
    return {
        "token": token,
        "expires_in": TOKEN_EXPIRE_H * 3600,
        "user": _user_to_dict(user),
        "project": _project_to_dict(proj),
    }


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
def list_projects(
    _: CurrentUser, db: DB,
    limit: int = Query(default=100, ge=1, le=1000),
    marker: str | None = None,
):
    q = select(Project).order_by(Project.created_at)
    if marker:
        ref = db.get(Project, marker)
        if ref:
            q = q.where(Project.created_at > ref.created_at)
    q = q.limit(limit)
    return {"items": [_project_to_dict(p) for p in db.scalars(q).all()]}


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


def _get_or_create_quota(db: Session, project_id: str) -> ProjectQuota:
    quota = db.get(ProjectQuota, project_id)
    if not quota:
        quota = ProjectQuota(project_id=project_id, max_instances=10, max_vcpus=20, max_ram_mb=32768, max_volume_gb=100)
        db.add(quota)
        db.commit()
        db.refresh(quota)
    return quota


@app.get("/projects/{ref}/quota")
def get_project_quota(ref: str, _: CurrentUser, db: DB):
    p = _get_project_by_ref(db, ref)
    q = _get_or_create_quota(db, p.id)
    return {
        "project_id": p.id,
        "max_instances": q.max_instances,
        "max_vcpus": q.max_vcpus,
        "max_ram_mb": q.max_ram_mb,
        "max_volume_gb": q.max_volume_gb,
    }


@app.put("/projects/{ref}/quota")
def update_project_quota(ref: str, payload: QuotaUpdate, _: AdminUser, db: DB):
    p = _get_project_by_ref(db, ref)
    q = _get_or_create_quota(db, p.id)
    for k, v in payload.model_dump(exclude_none=True).items():
        setattr(q, k, v)
    q.updated_at = utcnow()
    db.commit()
    db.refresh(q)
    return {
        "project_id": p.id,
        "max_instances": q.max_instances,
        "max_vcpus": q.max_vcpus,
        "max_ram_mb": q.max_ram_mb,
        "max_volume_gb": q.max_volume_gb,
    }


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
def list_users(
    _: AdminUser, db: DB,
    limit: int = Query(default=100, ge=1, le=1000),
    marker: str | None = None,
):
    q = select(User).order_by(User.created_at)
    if marker:
        ref = db.get(User, marker)
        if ref:
            q = q.where(User.created_at > ref.created_at)
    q = q.limit(limit)
    return {"items": [_user_to_dict(u) for u in db.scalars(q).all()]}


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


@app.post("/v3/users/{user_ref}/application_credentials", status_code=201)
def create_app_credential(user_ref: str, payload: AppCredIn, current: CurrentUser, db: DB):
    """Create an application credential for a user."""
    u = _get_user_by_ref(db, user_ref)
    # Users can only create creds for themselves; admins can do it for anyone
    if current.get("role") != "admin" and u.id != current["sub"]:
        raise HTTPException(status_code=403, detail="forbidden")
    if db.scalar(select(ApplicationCredential).where(
        ApplicationCredential.name == payload.name,
        ApplicationCredential.user_id == u.id,
    )):
        raise HTTPException(status_code=409, detail="application credential name already exists for this user")
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
        roles=",".join(payload.roles) if payload.roles else "member",
        secret_hash=_hash_password(secret_plain),
        expires_at=expires_dt,
    )
    db.add(ac)
    db.commit()
    db.refresh(ac)
    return _appcred_to_dict(ac, secret=secret_plain)


@app.get("/v3/users/{user_ref}/application_credentials")
def list_app_credentials(user_ref: str, current: CurrentUser, db: DB):
    """List application credentials for a user."""
    u = _get_user_by_ref(db, user_ref)
    if current.get("role") != "admin" and u.id != current["sub"]:
        raise HTTPException(status_code=403, detail="forbidden")
    creds = db.scalars(
        select(ApplicationCredential).where(ApplicationCredential.user_id == u.id)
    ).all()
    return {"application_credentials": [_appcred_to_dict(c) for c in creds]}


@app.get("/v3/users/{user_ref}/application_credentials/{cred_ref}")
def show_app_credential(user_ref: str, cred_ref: str, current: CurrentUser, db: DB):
    """Show a specific application credential."""
    u = _get_user_by_ref(db, user_ref)
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


@app.delete("/v3/users/{user_ref}/application_credentials/{cred_ref}", status_code=204)
def delete_app_credential(user_ref: str, cred_ref: str, current: CurrentUser, db: DB):
    """Delete an application credential."""
    u = _get_user_by_ref(db, user_ref)
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


@app.post("/auth/tokens/application-credential", status_code=201)
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
    if not _verify_password(payload.application_credential_secret, ac.secret_hash):
        raise HTTPException(status_code=401, detail="invalid application credential secret")
    if ac.expires_at and ac.expires_at < utcnow():
        raise HTTPException(status_code=401, detail="application credential has expired")
    user = db.get(User, ac.user_id)
    if not user or not user.enabled:
        raise HTTPException(status_code=403, detail="user disabled")
    roles = ac.roles.split(",") if ac.roles else ["member"]
    token = _create_token(user, scope_type="project", extra_roles=roles)
    return {"token": token, "expires_in": TOKEN_EXPIRE_H * 3600, "method": "application_credential"}


# ── Service Catalog ────────────────────────────────────────────────────────────

class ServiceIn(BaseModel):
    name: str
    type: str
    description: str | None = None
    enabled: bool = True


class EndpointIn(BaseModel):
    service_id: str
    interface: str = "public"
    region: str = "RegionOne"
    url: str
    enabled: bool = True


def _svc_to_dict(svc: ServiceCatalogEntry) -> dict:
    return {
        "id": svc.id, "name": svc.name, "type": svc.type,
        "description": svc.description, "enabled": svc.enabled,
        "created_at": svc.created_at.isoformat(),
    }


def _ep_to_dict(ep: ServiceEndpoint) -> dict:
    return {
        "id": ep.id, "service_id": ep.service_id,
        "interface": ep.interface, "region": ep.region,
        "url": ep.url, "enabled": ep.enabled,
    }


@app.post("/v3/services", status_code=201)
def create_service(payload: ServiceIn, _: AdminUser, db: DB):
    if db.scalar(select(ServiceCatalogEntry).where(ServiceCatalogEntry.name == payload.name)):
        raise HTTPException(status_code=409, detail="service already exists")
    svc = ServiceCatalogEntry(**payload.model_dump())
    db.add(svc)
    db.commit()
    db.refresh(svc)
    return _svc_to_dict(svc)


@app.get("/v3/services")
def list_services(_: CurrentUser, db: DB):
    svcs = db.scalars(select(ServiceCatalogEntry).order_by(ServiceCatalogEntry.name)).all()
    return {"items": [_svc_to_dict(s) for s in svcs]}


@app.delete("/v3/services/{service_id}", status_code=204)
def delete_service(service_id: str, _: AdminUser, db: DB):
    svc = db.get(ServiceCatalogEntry, service_id)
    if not svc:
        raise HTTPException(status_code=404, detail="service not found")
    db.delete(svc)
    db.commit()


@app.post("/v3/endpoints", status_code=201)
def create_endpoint(payload: EndpointIn, _: AdminUser, db: DB):
    svc = db.get(ServiceCatalogEntry, payload.service_id)
    if not svc:
        raise HTTPException(status_code=404, detail="service not found")
    ep = ServiceEndpoint(**payload.model_dump())
    db.add(ep)
    db.commit()
    db.refresh(ep)
    return _ep_to_dict(ep)


@app.get("/v3/endpoints")
def list_endpoints(_: CurrentUser, db: DB, service_id: str | None = None):
    q = select(ServiceEndpoint).order_by(ServiceEndpoint.created_at)
    if service_id:
        q = q.where(ServiceEndpoint.service_id == service_id)
    return {"items": [_ep_to_dict(ep) for ep in db.scalars(q).all()]}


@app.delete("/v3/endpoints/{endpoint_id}", status_code=204)
def delete_endpoint(endpoint_id: str, _: AdminUser, db: DB):
    ep = db.get(ServiceEndpoint, endpoint_id)
    if not ep:
        raise HTTPException(status_code=404, detail="endpoint not found")
    db.delete(ep)
    db.commit()


@app.get("/v3/auth/catalog")
def get_catalog(current: CurrentUser, db: DB):
    """Return the full service catalog for the authenticated user."""
    return {"catalog": _build_catalog(db)}


# ── Domain Management (Keystone v3) ──────────────────────────────────────────────────────

class DomainIn(BaseModel):
    name: str
    description: str | None = None
    enabled: bool = True


class DomainUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    enabled: bool | None = None


def _domain_to_dict(d: Domain) -> dict:
    return {
        "id": d.id,
        "name": d.name,
        "description": d.description,
        "enabled": d.enabled,
        "created_at": d.created_at.isoformat(),
    }


@app.post("/v3/domains", status_code=201)
def create_domain(payload: DomainIn, _: AdminUser, db: DB):
    """Create a new domain (admin only)."""
    if db.scalar(select(Domain).where(Domain.name == payload.name)):
        raise HTTPException(status_code=409, detail="domain already exists")
    domain = Domain(**payload.model_dump())
    db.add(domain)
    db.commit()
    db.refresh(domain)
    return _domain_to_dict(domain)


@app.get("/v3/domains")
def list_domains(current: CurrentUser, db: DB):
    """List all domains."""
    domains = db.scalars(select(Domain).order_by(Domain.name)).all()
    return {"domains": [_domain_to_dict(d) for d in domains]}


@app.get("/v3/domains/{domain_id}")
def show_domain(domain_id: str, current: CurrentUser, db: DB):
    """Get a specific domain by ID or name."""
    domain = db.get(Domain, domain_id)
    if not domain:
        domain = db.scalar(select(Domain).where(Domain.name == domain_id))
    if not domain:
        raise HTTPException(status_code=404, detail="domain not found")
    return _domain_to_dict(domain)


@app.patch("/v3/domains/{domain_id}")
def update_domain(domain_id: str, payload: DomainUpdate, _: AdminUser, db: DB):
    """Update domain properties (admin only)."""
    domain = db.get(Domain, domain_id)
    if not domain:
        raise HTTPException(status_code=404, detail="domain not found")
    if domain.id == "default" and payload.enabled is False:
        raise HTTPException(status_code=403, detail="cannot disable the default domain")
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(domain, field, value)
    db.commit()
    db.refresh(domain)
    return _domain_to_dict(domain)


@app.delete("/v3/domains/{domain_id}", status_code=204)
def delete_domain(domain_id: str, _: AdminUser, db: DB):
    """Delete a domain (admin only). Cannot delete the default domain."""
    if domain_id == "default":
        raise HTTPException(status_code=403, detail="cannot delete the default domain")
    domain = db.get(Domain, domain_id)
    if not domain:
        raise HTTPException(status_code=404, detail="domain not found")
    db.delete(domain)
    db.commit()


# ── Central Policy (oslo.policy) ───────────────────────────────────────────────

@app.get("/policy")
def get_policy(_: CurrentUser):
    """Return the central RBAC policy map for all CloudX services."""
    return {"policy": policy_list_rules()}


@app.post("/policy/enforce")
def check_policy(body: dict, current: CurrentUser):
    """Check if the current user can perform an action (for debugging/UI)."""
    action = body.get("action", "")
    if not action:
        raise HTTPException(status_code=400, detail="'action' is required")
    target_project = body.get("target_project_id")
    result = policy_enforce(action, current, target_project, raise_on_deny=False)
    return {"action": action, "allowed": result, "role": current.get("role")}


# ── Centralized Audit Logging ──────────────────────────────────────────────────

class AuditLogIn(BaseModel):
    user_id: str | None = None
    username: str | None = None
    project_id: str | None = None
    action: str
    resource_type: str
    resource_id: str | None = None
    details: str | None = None


@app.post("/audit-logs", status_code=201)
def create_audit_log(payload: AuditLogIn, db: DB):
    log_entry = AuditLog(
        user_id=payload.user_id,
        username=payload.username,
        project_id=payload.project_id,
        action=payload.action,
        resource_type=payload.resource_type,
        resource_id=payload.resource_id,
        details=payload.details,
    )
    db.add(log_entry)
    db.commit()
    db.refresh(log_entry)
    return {
        "id": log_entry.id,
        "user_id": log_entry.user_id,
        "username": log_entry.username,
        "project_id": log_entry.project_id,
        "action": log_entry.action,
        "resource_type": log_entry.resource_type,
        "resource_id": log_entry.resource_id,
        "details": log_entry.details,
        "timestamp": log_entry.timestamp.isoformat() if log_entry.timestamp else None,
    }


@app.get("/audit-logs")
def list_audit_logs(
    db: DB,
    project_id: str | None = None,
    resource_type: str | None = None,
    limit: int = 100,
):
    query = select(AuditLog).order_by(AuditLog.timestamp.desc())
    if project_id:
        query = query.where(AuditLog.project_id == project_id)
    if resource_type:
        query = query.where(AuditLog.resource_type == resource_type)
    query = query.limit(limit)

    rows = db.scalars(query).all()
    return {
        "items": [
            {
                "id": r.id,
                "user_id": r.user_id,
                "username": r.username,
                "project_id": r.project_id,
                "action": r.action,
                "resource_type": r.resource_type,
                "resource_id": r.resource_id,
                "details": r.details,
                "timestamp": r.timestamp.isoformat() if r.timestamp else None,
            }
            for r in rows
        ]
    }


# ── API Version Discovery ──────────────────────────────────────────────────────

@app.get("/v3")
def keystone_v3_root():
    """Keystone v3 API version discovery."""
    return {
        "version": {
            "id": "v3",
            "status": "stable",
            "updated": "2024-01-01T00:00:00Z",
            "links": [{"rel": "self", "href": "/v3"}],
            "media-types": [
                {"base": "application/json", "type": "application/vnd.openstack.identity-v3+json"}
            ],
        }
    }


@app.get("/")
def auth_root():
    """Keystone API root — version discovery."""
    return {
        "versions": {
            "values": [{
                "id": "v3",
                "status": "stable",
                "links": [{"rel": "self", "href": "/v3"}],
            }]
        }
    }
