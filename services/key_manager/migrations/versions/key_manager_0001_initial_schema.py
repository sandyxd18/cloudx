"""initial schema for key_manager

Revision ID: key_manager_0001
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

revision = "key_manager_0001"
down_revision = None
branch_labels = None
depends_on = None

metadata = sa.MetaData()

sa.Table(
    "key_manager_containers", metadata,
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column("name", sa.String(128), nullable=False),
    sa.Column("container_type", sa.String(32), nullable=False),
    sa.Column("secret_refs_json", sa.Text(), nullable=False),
    sa.Column("project_id", sa.String(64), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.Index('ix_key_manager_containers_name', 'name'),
    sa.Index('ix_key_manager_containers_project_id', 'project_id'),
)

sa.Table(
    "key_manager_secrets", metadata,
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column("name", sa.String(128), nullable=False),
    sa.Column("secret_type", sa.String(32), nullable=False),
    sa.Column("encrypted_payload", sa.Text(), nullable=False),
    sa.Column("algorithm", sa.String(32)),
    sa.Column("bit_length", sa.Integer()),
    sa.Column("mode", sa.String(32)),
    sa.Column("project_id", sa.String(64), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.Index('ix_key_manager_secrets_name', 'name'),
    sa.Index('ix_key_manager_secrets_project_id', 'project_id'),
)


def upgrade() -> None:
    bind = op.get_bind()
    metadata.create_all(bind, checkfirst=True)
    add_missing_columns(bind, metadata)


def downgrade() -> None:
    # Baseline: the tables stay (dropping a service's data is never an implicit side effect of `downgrade`).
    pass
