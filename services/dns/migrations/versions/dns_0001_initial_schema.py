"""initial schema for dns

Revision ID: dns_0001
Revises:
Create Date: 2026-10-18
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "dns_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    have = set(inspect(op.get_bind()).get_table_names())
    if "dns_zones" not in have:
        op.create_table(
            "dns_zones",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("project_id", sa.String(), nullable=True),
            sa.Column("kind", sa.String(), nullable=False),
            sa.Column("scope", sa.String(), nullable=False),
            sa.Column("email", sa.String(), nullable=False),
            sa.Column("ttl", sa.Integer(), nullable=False),
            sa.Column("serial", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_dns_zones_name", "dns_zones", ["name"], unique=True)
        op.create_index("ix_dns_zones_project_id", "dns_zones", ["project_id"])
    if "dns_recordsets" not in have:
        op.create_table(
            "dns_recordsets",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("zone_id", sa.String(), sa.ForeignKey("dns_zones.id", ondelete="CASCADE"), nullable=False),
            sa.Column("project_id", sa.String(), nullable=True),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("type", sa.String(), nullable=False),
            sa.Column("ttl", sa.Integer(), nullable=True),
            sa.Column("records", sa.JSON(), nullable=False),
            sa.Column("managed_by", sa.String(), nullable=True),
            sa.UniqueConstraint("zone_id", "name", "type"),
        )
        op.create_index("ix_dns_recordsets_zone_id", "dns_recordsets", ["zone_id"])
        op.create_index("ix_dns_recordsets_project_id", "dns_recordsets", ["project_id"])
        op.create_index("ix_dns_recordsets_managed_by", "dns_recordsets", ["managed_by"])
    if "dns_secondaries" not in have:
        op.create_table(
            "dns_secondaries",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("zone_id", sa.String(), sa.ForeignKey("dns_zones.id", ondelete="CASCADE"), nullable=False),
            sa.Column("address", sa.String(), nullable=False),
            sa.UniqueConstraint("zone_id", "address"),
        )
        op.create_index("ix_dns_secondaries_zone_id", "dns_secondaries", ["zone_id"])


def downgrade() -> None:
    pass