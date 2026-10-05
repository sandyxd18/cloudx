from pydantic import BaseModel, Field, field_validator


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
    project: str | None = None


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


class SwitchProjectIn(BaseModel):
    project_id: str


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


# ── Domain Management (Identity v3) ──────────────────────────────────────────────────────

class DomainIn(BaseModel):
    name: str
    description: str | None = None
    enabled: bool = True


class DomainUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    enabled: bool | None = None


# ── Centralized Audit Logging ──────────────────────────────────────────────────

class AuditLogIn(BaseModel):
    user_id: str | None = None
    username: str | None = None
    project_id: str | None = None
    action: str
    resource_type: str
    resource_id: str | None = None
    details: str | None = None
