"""delivery retry columns for event

Revision ID: event_0002
Revises: event_0001
Create Date: 2026-10-06

Idempotent: adds next_retry_at and last_error to event_webhook_deliveries if missing.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "event_0002"
down_revision = "event_0001"
branch_labels = None
depends_on = None

TABLE = "event_webhook_deliveries"


def upgrade() -> None:
    columns = {c["name"] for c in inspect(op.get_bind()).get_columns(TABLE)}
    if "next_retry_at" not in columns:
        op.add_column(TABLE, sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True))
    if "last_error" not in columns:
        op.add_column(TABLE, sa.Column("last_error", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column(TABLE, "last_error")
    op.drop_column(TABLE, "next_retry_at")
