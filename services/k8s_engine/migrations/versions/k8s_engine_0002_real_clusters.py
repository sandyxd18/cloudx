"""Migration for real clusters and k8s nodes in k8s_engine

Revision ID: k8s_engine_0002
Revises: k8s_engine_0001
Create Date: 2026-10-16
"""
from alembic import op
import sqlalchemy as sa
from shared.schema import add_missing_columns

revision = "k8s_engine_0002"
down_revision = "k8s_engine_0001"
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
    sa.Column("join_token", sa.String()),
    sa.Column("admin_token", sa.String()),
    sa.Column("status_reason", sa.String()),
    sa.Column("master_ip", sa.String()),
    sa.Column("ready_at", sa.DateTime()),
    sa.ForeignKeyConstraint(['template_id'], ['k8s_cluster_templates.id']),
)

sa.Table(
    "k8s_nodes", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("cluster_id", sa.String(), nullable=False),
    sa.Column("server_id", sa.String(), nullable=False),
    sa.Column("role", sa.String(), nullable=False),
    sa.Column("ip", sa.String()),
    sa.Column("ready", sa.Boolean(), default=False),
    sa.ForeignKeyConstraint(['cluster_id'], ['k8s_clusters.id']),
)


def upgrade() -> None:
    bind = op.get_bind()
    metadata.create_all(bind, checkfirst=True)
    add_missing_columns(bind, metadata)


def downgrade() -> None:
    pass
