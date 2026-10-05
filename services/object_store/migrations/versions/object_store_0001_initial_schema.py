"""initial schema for object_store

Revision ID: object_store_0001
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

revision = "object_store_0001"
down_revision = None
branch_labels = None
depends_on = None

metadata = sa.MetaData()

sa.Table(
    "object_store_bucket_types", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("name", sa.String(), nullable=False),
    sa.Column("driver", sa.String(), nullable=False),
    sa.Column("config", sa.String(), nullable=False),
    sa.Column("description", sa.String()),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Index('ix_object_store_bucket_types_name', 'name', unique=True),
)

sa.Table(
    "object_store_buckets", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("name", sa.String(), nullable=False),
    sa.Column("project_id", sa.String(), nullable=False),
    sa.Column("bucket_type_id", sa.String(), nullable=False),
    sa.Column("visibility", sa.String(), nullable=False),
    sa.Column("status", sa.String(), nullable=False),
    sa.Column("size_bytes", sa.Integer(), nullable=False),
    sa.Column("object_count", sa.Integer(), nullable=False),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['bucket_type_id'], ['object_store_bucket_types.id']),
    sa.Index('ix_object_store_buckets_name', 'name'),
    sa.Index('ix_object_store_buckets_project_id', 'project_id'),
)

sa.Table(
    "object_store_objects", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("bucket_id", sa.String(), nullable=False),
    sa.Column("key", sa.String(), nullable=False),
    sa.Column("size", sa.Integer(), nullable=False),
    sa.Column("content_type", sa.String(), nullable=False),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['bucket_id'], ['object_store_buckets.id'], ondelete='CASCADE'),
    sa.Index('ix_object_store_objects_key', 'key'),
)


def upgrade() -> None:
    bind = op.get_bind()
    metadata.create_all(bind, checkfirst=True)
    add_missing_columns(bind, metadata)


def downgrade() -> None:
    # Baseline: the tables stay (dropping a service's data is never an implicit side effect of `downgrade`).
    pass
