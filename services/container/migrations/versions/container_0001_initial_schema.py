"""initial schema for container

Revision ID: container_0001
Revises:
Create Date: 2026-10-19
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "container_0001"
down_revision = None
branch_labels = None
depends_on = None

def upgrade() -> None:
    have = set(inspect(op.get_bind()).get_table_names())
    if "ct_containers" not in have:
        op.create_table(
            "ct_containers",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("project_id", sa.String(), nullable=False),
            sa.Column("image", sa.String(), nullable=False),
            sa.Column("command", sa.JSON(), nullable=False),
            sa.Column("env", sa.JSON(), nullable=False),
            sa.Column("cpu", sa.Float(), nullable=False),
            sa.Column("memory_mb", sa.Integer(), nullable=False),
            sa.Column("network_id", sa.String(), nullable=False),
            sa.Column("subnet_id", sa.String(), nullable=True),
            sa.Column("port_id", sa.String(), nullable=True),
            sa.Column("port_name", sa.String(), nullable=True),
            sa.Column("ip", sa.String(), nullable=True),
            sa.Column("mac", sa.String(), nullable=True),
            sa.Column("prefix", sa.Integer(), nullable=True),
            sa.Column("gateway", sa.String(), nullable=True),
            sa.Column("dns", sa.JSON(), nullable=False),
            sa.Column("host", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("want", sa.String(), nullable=False),
            sa.Column("logs", sa.Text(), nullable=True),
            sa.Column("error", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("project_id", "name"),
        )
        op.create_index("ix_ct_containers_project_id", "ct_containers", ["project_id"])
        op.create_index("ix_ct_containers_host", "ct_containers", ["host"])
    if "ct_hosts" not in have:
        op.create_table(
            "ct_hosts",
            sa.Column("host", sa.String(), primary_key=True),
            sa.Column("seen_at", sa.DateTime(), nullable=False),
        )

def downgrade() -> None:
    pass