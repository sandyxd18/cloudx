"""initial schema for file_store

Revision ID: file_store_0001
Revises:
Create Date: 2026-10-18
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "file_store_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    have = set(inspect(op.get_bind()).get_table_names())
    if "fs_share_networks" not in have:
        op.create_table(
            "fs_share_networks",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("project_id", sa.String(), nullable=False),
            sa.Column("network_id", sa.String(), nullable=False),
            sa.Column("subnet_id", sa.String(), nullable=True),
            sa.Column("port_id", sa.String(), nullable=True),
            sa.Column("port_name", sa.String(), nullable=True),
            sa.Column("ip", sa.String(), nullable=True),
            sa.Column("mac", sa.String(), nullable=True),
            sa.Column("prefix", sa.Integer(), nullable=True),
            sa.Column("gateway", sa.String(), nullable=True),
            sa.Column("switch", sa.String(), nullable=True),
            sa.Column("host", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_fs_share_networks_project_id", "fs_share_networks", ["project_id"])
    if "fs_shares" not in have:
        op.create_table(
            "fs_shares",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("project_id", sa.String(), nullable=False),
            sa.Column("share_network_id", sa.String(), sa.ForeignKey("fs_share_networks.id"), nullable=False),
            sa.Column("size_gb", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("project_id", "name"),
        )
        op.create_index("ix_fs_shares_project_id", "fs_shares", ["project_id"])
        op.create_index("ix_fs_shares_share_network_id", "fs_shares", ["share_network_id"])
    if "fs_access_rules" not in have:
        op.create_table(
            "fs_access_rules",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("share_id", sa.String(), sa.ForeignKey("fs_shares.id"), nullable=False),
            sa.Column("to_cidr", sa.String(), nullable=False),
            sa.Column("level", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("share_id", "to_cidr"),
        )
        op.create_index("ix_fs_access_rules_share_id", "fs_access_rules", ["share_id"])


def downgrade() -> None:
    pass
