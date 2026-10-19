"""initial schema for ha

Revision ID: ha_0001
Revises:
Create Date: 2026-10-19
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "ha_0001"
down_revision = None
branch_labels = None
depends_on = None

def upgrade() -> None:
    have = set(inspect(op.get_bind()).get_table_names())
    if "ha_hosts" not in have:
        op.create_table(
            "ha_hosts",
            sa.Column("host", sa.String(), primary_key=True),
            sa.Column("initiator_iqn", sa.String(), nullable=True),
            sa.Column("last_seen", sa.DateTime(), nullable=False),
            sa.Column("state", sa.String(), nullable=False),
            sa.Column("fails", sa.Integer(), nullable=False),
            sa.Column("suspect_at", sa.DateTime(), nullable=True),
        )
    if "ha_protected" not in have:
        op.create_table(
            "ha_protected",
            sa.Column("server_id", sa.String(), primary_key=True),
            sa.Column("project_id", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_ha_protected_project_id", "ha_protected", ["project_id"])
    if "ha_recoveries" not in have:
        op.create_table(
            "ha_recoveries",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("host", sa.String(), nullable=False),
            sa.Column("server_id", sa.String(), nullable=True),
            sa.Column("project_id", sa.String(), nullable=True),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("message", sa.String(), nullable=True),
            sa.Column("started_at", sa.DateTime(), nullable=False),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_ha_recoveries_project_id", "ha_recoveries", ["project_id"])

def downgrade() -> None:
    pass
