import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


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
    __table_args__ = (UniqueConstraint("domain_id", "name", name="uq_auth_projects_domain_name"),)
    name:        Mapped[str] = mapped_column(String, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled:     Mapped[bool] = mapped_column(Boolean, default=True)
    domain_id:   Mapped[str] = mapped_column(String, ForeignKey("auth_domains.id"), default="default", server_default="default")
    created_at:  Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ProjectQuota(Base):
    """Quota limits per project."""
    __tablename__ = "auth_project_quotas"
    project_id:    Mapped[str] = mapped_column(String, ForeignKey("auth_projects.id", ondelete="CASCADE"), primary_key=True)
    max_instances: Mapped[int] = mapped_column(Integer, default=10)
    max_vcpus:     Mapped[int] = mapped_column(Integer, default=20)
    max_ram_mb:    Mapped[int] = mapped_column(Integer, default=32768)
    max_volume_gb: Mapped[int] = mapped_column(Integer, default=100)
    max_networks: Mapped[int] = mapped_column(Integer, default=100, server_default="100")
    max_subnets: Mapped[int] = mapped_column(Integer, default=100, server_default="100")
    max_ports: Mapped[int] = mapped_column(Integer, default=500, server_default="500")
    max_routers: Mapped[int] = mapped_column(Integer, default=10, server_default="10")
    max_floating_ips: Mapped[int] = mapped_column(Integer, default=50, server_default="50")
    max_security_groups: Mapped[int] = mapped_column(Integer, default=20, server_default="20")
    max_zones: Mapped[int] = mapped_column(Integer, default=10, server_default="10")
    max_recordsets: Mapped[int] = mapped_column(Integer, default=200, server_default="200")
    updated_at:    Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class User(Base):
    __tablename__ = "auth_users"
    id:            Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    __table_args__ = (UniqueConstraint("domain_id", "username", name="uq_auth_users_domain_username"),)
    username:      Mapped[str] = mapped_column(String, index=True)
    email:         Mapped[str] = mapped_column(String, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String)
    project_id:    Mapped[str] = mapped_column(String, ForeignKey("auth_projects.id"))
    role:          Mapped[str] = mapped_column(String, default="member")   # admin | member | reader
    enabled:       Mapped[bool] = mapped_column(Boolean, default=True)
    domain_id:     Mapped[str] = mapped_column(String, ForeignKey("auth_domains.id"), default="default", server_default="default")
    is_domain_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
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
    """CloudX Identity Domain (top-level organizational unit)."""
    __tablename__ = "auth_domains"
    id:          Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name:        Mapped[str] = mapped_column(String, unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled:     Mapped[bool] = mapped_column(Boolean, default=True)
    created_at:  Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class DomainQuota(Base):
    """Aggregate caps across every project of a domain (NULL = no cap)."""
    __tablename__ = "auth_domain_quotas"
    domain_id: Mapped[str] = mapped_column(String, ForeignKey("auth_domains.id", ondelete="CASCADE"), primary_key=True)
    max_instances: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_vcpus: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_ram_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_volume_gb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_networks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_subnets: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_ports: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_routers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_floating_ips: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_security_groups: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_zones: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_recordsets: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ApplicationCredential(Base):
    """Application Credentials — pre-scoped tokens for automation/CI."""
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

class RoleAssignment(Base):
    """Per-project role of a user; overrides User.role (the default) inside that project."""
    __tablename__ = "auth_role_assignments"
    user_id:    Mapped[str] = mapped_column(String, ForeignKey("auth_users.id", ondelete="CASCADE"), primary_key=True)
    project_id: Mapped[str] = mapped_column(String, ForeignKey("auth_projects.id", ondelete="CASCADE"), primary_key=True)
    role:       Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
