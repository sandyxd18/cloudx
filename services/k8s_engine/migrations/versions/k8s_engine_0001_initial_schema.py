"""initial schema for k8s_engine

Revision ID: k8s_engine_0001
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

revision = "k8s_engine_0001"
down_revision = None
branch_labels = None
depends_on = None

metadata = sa.MetaData()

sa.Table(
    "k8s_cluster_templates", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("name", sa.String(), nullable=False),
    sa.Column("k8s_version", sa.String()),
    sa.Column("master_flavor", sa.String()),
    sa.Column("worker_flavor", sa.String()),
    sa.Column("image_id", sa.String(), nullable=False),
    sa.Column("network_driver", sa.String()),
    sa.Column("volume_driver", sa.String()),
    sa.Column("project_id", sa.String(), nullable=False),
    sa.Column("created_at", sa.DateTime()),
)

sa.Table(
    "k8s_clusters", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("name", sa.String(), nullable=False),
    sa.Column("template_id", sa.String(), nullable=False),
    sa.Column("master_count", sa.Integer()),
    sa.Column("worker_count", sa.Integer()),
    sa.Column("network_id", sa.String(), nullable=False),
    sa.Column("status", sa.String()),
    sa.Column("project_id", sa.String(), nullable=False),
    sa.Column("created_at", sa.DateTime()),
    sa.ForeignKeyConstraint(['template_id'], ['k8s_cluster_templates.id']),
)


def upgrade() -> None:
    bind = op.get_bind()
    metadata.create_all(bind, checkfirst=True)
    add_missing_columns(bind, metadata)


def downgrade() -> None:
    # Baseline: the tables stay (dropping a service's data is never an implicit side effect of `downgrade`).
    pass
