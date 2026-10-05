"""initial schema for event

Revision ID: event_0001
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

revision = "event_0001"
down_revision = None
branch_labels = None
depends_on = None

metadata = sa.MetaData()

sa.Table(
    "event_logs", metadata,
    sa.Column("id", sa.Integer(), primary_key=True),
    sa.Column("event_type", sa.String(64), nullable=False),
    sa.Column("resource_type", sa.String(32), nullable=False),
    sa.Column("resource_id", sa.String(64)),
    sa.Column("project_id", sa.String(64)),
    sa.Column("user_id", sa.String(64)),
    sa.Column("payload", sa.Text()),
    sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
    sa.Index('ix_event_logs_event_type', 'event_type'),
    sa.Index('ix_event_logs_project_id', 'project_id'),
    sa.Index('ix_event_logs_resource_id', 'resource_id'),
    sa.Index('ix_event_logs_resource_type', 'resource_type'),
    sa.Index('ix_event_logs_timestamp', 'timestamp'),
)

sa.Table(
    "event_webhook_subscriptions", metadata,
    sa.Column("id", sa.Integer(), primary_key=True),
    sa.Column("project_id", sa.String(64), nullable=False),
    sa.Column("name", sa.String(128), nullable=False),
    sa.Column("endpoint_url", sa.String(512), nullable=False),
    sa.Column("event_types", sa.String(512), nullable=False),
    sa.Column("secret", sa.String(128)),
    sa.Column("is_active", sa.Boolean(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Index('ix_event_webhook_subscriptions_project_id', 'project_id'),
)

sa.Table(
    "event_webhook_deliveries", metadata,
    sa.Column("id", sa.Integer(), primary_key=True),
    sa.Column("subscription_id", sa.Integer(), nullable=False),
    sa.Column("event_id", sa.Integer(), nullable=False),
    sa.Column("status", sa.String(16), nullable=False),
    sa.Column("response_code", sa.Integer()),
    sa.Column("attempts", sa.Integer(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['event_id'], ['event_logs.id']),
    sa.ForeignKeyConstraint(['subscription_id'], ['event_webhook_subscriptions.id']),
)


def upgrade() -> None:
    bind = op.get_bind()
    metadata.create_all(bind, checkfirst=True)
    add_missing_columns(bind, metadata)


def downgrade() -> None:
    # Baseline: the tables stay (dropping a service's data is never an implicit side effect of `downgrade`).
    pass
