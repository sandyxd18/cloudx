"""initial schema for placement

Revision ID: placement_0001
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

revision = "placement_0001"
down_revision = None
branch_labels = None
depends_on = None

metadata = sa.MetaData()

sa.Table(
    "placement_custom_resource_classes", metadata,
    sa.Column("name", sa.String(), primary_key=True),
    sa.Column("created_at", sa.DateTime(), nullable=False),
)

sa.Table(
    "placement_resource_providers", metadata,
    sa.Column("uuid", sa.String(), primary_key=True),
    sa.Column("name", sa.String(), nullable=False),
    sa.Column("parent_provider_id", sa.String()),
    sa.Column("generation", sa.Integer(), nullable=False),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.UniqueConstraint('name'),
)

sa.Table(
    "placement_traits", metadata,
    sa.Column("name", sa.String(), primary_key=True),
)

sa.Table(
    "placement_allocations", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("consumer_uuid", sa.String(), nullable=False),
    sa.Column("project_id", sa.String(), nullable=False),
    sa.Column("resource_provider_id", sa.String(), nullable=False),
    sa.Column("resource_class", sa.String(), nullable=False),
    sa.Column("used", sa.Integer(), nullable=False),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['resource_provider_id'], ['placement_resource_providers.uuid']),
    sa.Index('ix_placement_allocations_consumer_uuid', 'consumer_uuid'),
    sa.Index('ix_placement_allocations_project_id', 'project_id'),
)

sa.Table(
    "placement_compute_node_heartbeats", metadata,
    sa.Column("resource_provider_id", sa.String(), primary_key=True),
    sa.Column("host", sa.String(), nullable=False),
    sa.Column("status", sa.String(), nullable=False),
    sa.Column("cpu_percent_used", sa.Float()),
    sa.Column("memory_mb_used", sa.Integer()),
    sa.Column("memory_mb_total", sa.Integer()),
    sa.Column("disk_gb_used", sa.Integer()),
    sa.Column("disk_gb_total", sa.Integer()),
    sa.Column("running_vms", sa.Integer(), nullable=False),
    sa.Column("capabilities", sa.JSON()),
    sa.Column("last_seen", sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['resource_provider_id'], ['placement_resource_providers.uuid']),
)

sa.Table(
    "placement_inventories", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("resource_provider_id", sa.String(), nullable=False),
    sa.Column("resource_class", sa.String(), nullable=False),
    sa.Column("total", sa.Integer(), nullable=False),
    sa.Column("reserved", sa.Integer(), nullable=False),
    sa.Column("min_unit", sa.Integer(), nullable=False),
    sa.Column("max_unit", sa.Integer(), nullable=False),
    sa.Column("step_size", sa.Integer(), nullable=False),
    sa.Column("allocation_ratio", sa.Float(), nullable=False),
    sa.ForeignKeyConstraint(['resource_provider_id'], ['placement_resource_providers.uuid']),
    sa.UniqueConstraint('resource_provider_id', 'resource_class'),
)

sa.Table(
    "placement_rp_aggregates", metadata,
    sa.Column("resource_provider_id", sa.String(), primary_key=True),
    sa.Column("aggregate_uuid", sa.String(), primary_key=True),
    sa.ForeignKeyConstraint(['resource_provider_id'], ['placement_resource_providers.uuid']),
)

sa.Table(
    "placement_rp_traits", metadata,
    sa.Column("resource_provider_id", sa.String(), primary_key=True),
    sa.Column("trait_name", sa.String(), primary_key=True),
    sa.ForeignKeyConstraint(['trait_name'], ['placement_traits.name']),
    sa.ForeignKeyConstraint(['resource_provider_id'], ['placement_resource_providers.uuid']),
)


def upgrade() -> None:
    bind = op.get_bind()
    metadata.create_all(bind, checkfirst=True)
    add_missing_columns(bind, metadata)


def downgrade() -> None:
    # Baseline: the tables stay (dropping a service's data is never an implicit side effect of `downgrade`).
    pass
