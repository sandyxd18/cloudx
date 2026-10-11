"""agent host heartbeats for load_balancer HA

Revision ID: load_balancer_0003
Revises: load_balancer_0002
Create Date: 2026-10-11
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "load_balancer_0003"
down_revision = "load_balancer_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = inspect(op.get_bind())
    tables = insp.get_table_names()
    if "lb_agent_hosts" not in tables:
        op.create_table(
            "lb_agent_hosts",
            sa.Column("host", sa.String(), primary_key=True),
            sa.Column("last_seen", sa.DateTime(), nullable=False),
        )


def downgrade() -> None:
    pass
