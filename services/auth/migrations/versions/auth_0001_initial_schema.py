"""initial schema for auth

Revision ID: auth_0001
Revises:
Create Date: 2026-10-05

Idempotent baseline. On a fresh database it creates every table. On a database built by an older release (when the
service still ran create_all()/ensure_columns() itself) it creates what is missing, adds missing columns,
so `cloudx-db-migrate` is safe to run against any existing install.

The table definitions are frozen here on purpose: never edit them, add a new revision instead.
"""
from alembic import op
import sqlalchemy as sa
from shared.schema import add_missing_columns

revision = "auth_0001"
down_revision = None
branch_labels = None
depends_on = None

metadata = sa.MetaData()

sa.Table(
    "auth_audit_logs", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("user_id", sa.String()),
    sa.Column("username", sa.String()),
    sa.Column("project_id", sa.String()),
    sa.Column("action", sa.String(), nullable=False),
    sa.Column("resource_type", sa.String(), nullable=False),
    sa.Column("resource_id", sa.String()),
    sa.Column("details", sa.Text()),
    sa.Column("timestamp", sa.DateTime(), nullable=False),
    sa.Index('ix_auth_audit_logs_action', 'action'),
    sa.Index('ix_auth_audit_logs_resource_type', 'resource_type'),
)

sa.Table(
    "auth_domains", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("name", sa.String(), nullable=False),
    sa.Column("description", sa.Text()),
    sa.Column("enabled", sa.Boolean(), nullable=False),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Index('ix_auth_domains_name', 'name', unique=True),
)

sa.Table(
    "auth_projects", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("name", sa.String(), nullable=False),
    sa.Column("description", sa.Text()),
    sa.Column("enabled", sa.Boolean(), nullable=False),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Index('ix_auth_projects_name', 'name', unique=True),
)

sa.Table(
    "auth_revoked_tokens", metadata,
    sa.Column("jti", sa.String(), primary_key=True),
    sa.Column("revoked_at", sa.DateTime(), nullable=False),
    sa.Column("expires_at", sa.DateTime(), nullable=False),
)

sa.Table(
    "auth_services", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("name", sa.String(), nullable=False),
    sa.Column("type", sa.String(), nullable=False),
    sa.Column("description", sa.Text()),
    sa.Column("enabled", sa.Boolean(), nullable=False),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Index('ix_auth_services_name', 'name', unique=True),
)

sa.Table(
    "auth_groups", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("name", sa.String(), nullable=False),
    sa.Column("description", sa.Text()),
    sa.Column("project_id", sa.String(), nullable=False),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['auth_projects.id']),
    sa.UniqueConstraint('name', 'project_id'),
    sa.Index('ix_auth_groups_name', 'name'),
)

sa.Table(
    "auth_project_quotas", metadata,
    sa.Column("project_id", sa.String(), primary_key=True),
    sa.Column("max_instances", sa.Integer(), nullable=False),
    sa.Column("max_vcpus", sa.Integer(), nullable=False),
    sa.Column("max_ram_mb", sa.Integer(), nullable=False),
    sa.Column("max_volume_gb", sa.Integer(), nullable=False),
    sa.Column("updated_at", sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['auth_projects.id'], ondelete='CASCADE'),
)

sa.Table(
    "auth_service_endpoints", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("service_id", sa.String(), nullable=False),
    sa.Column("interface", sa.String(), nullable=False),
    sa.Column("region", sa.String(), nullable=False),
    sa.Column("url", sa.String(), nullable=False),
    sa.Column("enabled", sa.Boolean(), nullable=False),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['service_id'], ['auth_services.id'], ondelete='CASCADE'),
)

sa.Table(
    "auth_users", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("username", sa.String(), nullable=False),
    sa.Column("email", sa.String(), nullable=False),
    sa.Column("password_hash", sa.String(), nullable=False),
    sa.Column("project_id", sa.String(), nullable=False),
    sa.Column("role", sa.String(), nullable=False),
    sa.Column("enabled", sa.Boolean(), nullable=False),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['auth_projects.id']),
    sa.Index('ix_auth_users_email', 'email', unique=True),
    sa.Index('ix_auth_users_username', 'username', unique=True),
)

sa.Table(
    "auth_access_keys", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("access_key_id", sa.String(), nullable=False),
    sa.Column("secret_key_hash", sa.String(), nullable=False),
    sa.Column("user_id", sa.String(), nullable=False),
    sa.Column("project_id", sa.String(), nullable=False),
    sa.Column("name", sa.String(), nullable=False),
    sa.Column("enabled", sa.Boolean(), nullable=False),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['auth_projects.id']),
    sa.ForeignKeyConstraint(['user_id'], ['auth_users.id']),
    sa.Index('ix_auth_access_keys_access_key_id', 'access_key_id', unique=True),
)

sa.Table(
    "auth_application_credentials", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("name", sa.String(), nullable=False),
    sa.Column("description", sa.Text()),
    sa.Column("user_id", sa.String(), nullable=False),
    sa.Column("project_id", sa.String(), nullable=False),
    sa.Column("roles", sa.String(), nullable=False),
    sa.Column("secret_hash", sa.String(), nullable=False),
    sa.Column("expires_at", sa.DateTime()),
    sa.Column("enabled", sa.Boolean(), nullable=False),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['auth_users.id']),
    sa.ForeignKeyConstraint(['project_id'], ['auth_projects.id']),
    sa.UniqueConstraint('name', 'user_id'),
    sa.Index('ix_auth_application_credentials_name', 'name'),
)

sa.Table(
    "auth_group_members", metadata,
    sa.Column("group_id", sa.String(), primary_key=True),
    sa.Column("user_id", sa.String(), primary_key=True),
    sa.ForeignKeyConstraint(['group_id'], ['auth_groups.id']),
    sa.ForeignKeyConstraint(['user_id'], ['auth_users.id']),
)


def upgrade() -> None:
    bind = op.get_bind()
    metadata.create_all(bind, checkfirst=True)
    add_missing_columns(bind, metadata)


def downgrade() -> None:
    # Baseline: the tables stay (dropping a service's data is never an implicit side effect of `downgrade`).
    pass
