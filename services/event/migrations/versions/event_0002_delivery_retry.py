"""delivery retry columns for event

Revision ID: event_0002
Revises: event_0001
Create Date: 2026-10-06

Idempotent: adds next_retry_at and last_error to event_webhook_deliveries if missing.
"""
from alembic import op

revision = "event_0002"
down_revision = "event_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE event_webhook_deliveries ADD COLUMN IF NOT EXISTS next_retry_at TIMESTAMP WITH TIME ZONE NULL")
    op.execute("ALTER TABLE event_webhook_deliveries ADD COLUMN IF NOT EXISTS last_error TEXT NULL")


def downgrade() -> None:
    pass
