"""initial schema for orchestration

Revision ID: orchestration_0001
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

revision = "orchestration_0001"
down_revision = None
branch_labels = None
depends_on = None

metadata = sa.MetaData()

sa.Table(
    "orchestration_scaling_groups", metadata,
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column("project_id", sa.String(64), nullable=False),
    sa.Column("name", sa.String(128), nullable=False),
    sa.Column("description", sa.String(255)),
    sa.Column("min_instances", sa.Integer(), nullable=False),
    sa.Column("max_instances", sa.Integer(), nullable=False),
    sa.Column("target_cpu_percent", sa.Integer(), nullable=False),
    sa.Column("cooldown_seconds", sa.Integer(), nullable=False),
    sa.Column("scale_in_cooldown_seconds", sa.Integer(), nullable=False),
    sa.Column("launch_template_id", sa.String(64), nullable=False),
    sa.Column("image_id", sa.String(64), nullable=False),
    sa.Column("flavor_id", sa.String(64), nullable=False),
    sa.Column("network_id", sa.String(64), nullable=False),
    sa.Column("load_balancer_pool_id", sa.String(64)),
    sa.Column("subnet_id", sa.String(64)),
    sa.Column("is_active", sa.Boolean(), nullable=False),
    sa.Column("status", sa.String(16), nullable=False),
    sa.Column("last_scale_out_at", sa.DateTime(timezone=True)),
    sa.Column("last_scale_in_at", sa.DateTime(timezone=True)),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.Index('ix_orchestration_scaling_groups_project_id', 'project_id'),
)

sa.Table(
    "orchestration_scaling_history", metadata,
    sa.Column("id", sa.Integer(), primary_key=True),
    sa.Column("group_id", sa.String(64), nullable=False),
    sa.Column("action", sa.String(16), nullable=False),
    sa.Column("reason", sa.String(255)),
    sa.Column("server_id", sa.String(64)),
    sa.Column("avg_cpu_before", sa.Double()),
    sa.Column("instance_count_before", sa.Integer()),
    sa.Column("instance_count_after", sa.Integer()),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Index('ix_orchestration_scaling_history_group_id', 'group_id'),
)

sa.Table(
    "orchestration_scaling_group_instances", metadata,
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column("group_id", sa.String(64), nullable=False),
    sa.Column("server_id", sa.String(64), nullable=False),
    sa.Column("server_name", sa.String(128), nullable=False),
    sa.Column("ip_address", sa.String(64)),
    sa.Column("port_id", sa.String(64)),
    sa.Column("status", sa.String(16), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['group_id'], ['orchestration_scaling_groups.id']),
    sa.Index('ix_orchestration_scaling_group_instances_group_id', 'group_id'),
    sa.Index('ix_orchestration_scaling_group_instances_server_id', 'server_id'),
)


def upgrade() -> None:
    bind = op.get_bind()
    metadata.create_all(bind, checkfirst=True)
    add_missing_columns(bind, metadata)


def downgrade() -> None:
    # Baseline: the tables stay (dropping a service's data is never an implicit side effect of `downgrade`).
    pass
