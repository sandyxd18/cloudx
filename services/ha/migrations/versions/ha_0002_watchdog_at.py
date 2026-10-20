"""ha_hosts.watchdog_at

Revision ID: ha_0002
Revises: ha_0001
Create Date: 2026-10-20
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "ha_0002"
down_revision = "ha_0001"
branch_labels = None
depends_on = None

def upgrade() -> None:
    cols = {c["name"] for c in inspect(op.get_bind()).get_columns("ha_hosts")}
    if "watchdog_at" not in cols:
        op.add_column("ha_hosts", sa.Column("watchdog_at", sa.DateTime(), nullable=True))

def downgrade() -> None:
    pass
