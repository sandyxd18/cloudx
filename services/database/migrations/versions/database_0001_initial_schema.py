"""initial schema for database

Revision ID: database_0001
Revises:
Create Date: 2026-10-19
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "database_0001"
down_revision = None
branch_labels = None
depends_on = None

def upgrade() -> None:
    have = set(inspect(op.get_bind()).get_table_names())
    if "db_instances" not in have:
        op.create_table(
            "db_instances",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("project_id", sa.String(), nullable=False),
            sa.Column("engine", sa.String(), nullable=False),
            sa.Column("backend", sa.String(), nullable=False),
            sa.Column("storage", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("error", sa.String(), nullable=True),
            sa.Column("network_id", sa.String(), nullable=False),
            sa.Column("size_gb", sa.Integer(), nullable=True),
            sa.Column("image", sa.String(), nullable=True),
            sa.Column("flavor", sa.String(), nullable=True),
            sa.Column("allowed_cidr", sa.String(), nullable=True),
            sa.Column("ip", sa.String(), nullable=True),
            sa.Column("sg_id", sa.String(), nullable=True),
            sa.Column("server_id", sa.String(), nullable=True),
            sa.Column("container_id", sa.String(), nullable=True),
            sa.Column("volume_id", sa.String(), nullable=True),
            sa.Column("share_id", sa.String(), nullable=True),
            sa.Column("share_net_id", sa.String(), nullable=True),
            sa.Column("rule_id", sa.String(), nullable=True),
            sa.Column("token_hash", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("project_id", "name"),
        )
        op.create_index("ix_db_instances_project_id", "db_instances", ["project_id"])

def downgrade() -> None:
    pass