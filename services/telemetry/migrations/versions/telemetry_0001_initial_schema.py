"""initial schema for telemetry

Revision ID: telemetry_0001
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

revision = "telemetry_0001"
down_revision = None
branch_labels = None
depends_on = None

metadata = sa.MetaData()

sa.Table(
    "telemetry_metric_types", metadata,
    sa.Column("id", sa.Integer(), primary_key=True),
    sa.Column("name", sa.String(32), nullable=False),
    sa.Column("description", sa.String(255)),
    sa.Column("unit", sa.String(16)),
    sa.Column("default_enabled", sa.Boolean(), nullable=False),
    sa.UniqueConstraint('name'),
)

sa.Table(
    "telemetry_metrics", metadata,
    sa.Column("id", sa.Integer(), primary_key=True),
    sa.Column("instance_id", sa.String(64), nullable=False),
    sa.Column("project_id", sa.String(64)),
    sa.Column("metric_type", sa.String(32), nullable=False),
    sa.Column("value", sa.Float(), nullable=False),
    sa.Column("unit", sa.String(16)),
    sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
    sa.Column("host", sa.String(64)),
    sa.Index('ix_telemetry_metrics_instance_id', 'instance_id'),
    sa.Index('ix_telemetry_metrics_metric_type', 'metric_type'),
    sa.Index('ix_telemetry_metrics_project_id', 'project_id'),
    sa.Index('ix_telemetry_metrics_timestamp', 'timestamp'),
)


def upgrade() -> None:
    bind = op.get_bind()
    metadata.create_all(bind, checkfirst=True)
    add_missing_columns(bind, metadata)


def downgrade() -> None:
    # Baseline: the tables stay (dropping a service's data is never an implicit side effect of `downgrade`).
    pass
