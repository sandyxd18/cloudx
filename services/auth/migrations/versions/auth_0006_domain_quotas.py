"""aggregate quotas per domain

Revision ID: auth_0006
Revises: auth_0005
Create Date: 2026-10-17
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "auth_0006"
down_revision = "auth_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "auth_domain_quotas" in inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "auth_domain_quotas",
        sa.Column("domain_id", sa.String(), sa.ForeignKey("auth_domains.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("max_instances", sa.Integer(), nullable=True),
        sa.Column("max_vcpus", sa.Integer(), nullable=True),
        sa.Column("max_ram_mb", sa.Integer(), nullable=True),
        sa.Column("max_volume_gb", sa.Integer(), nullable=True),
        sa.Column("max_networks", sa.Integer(), nullable=True),
        sa.Column("max_subnets", sa.Integer(), nullable=True),
        sa.Column("max_ports", sa.Integer(), nullable=True),
        sa.Column("max_routers", sa.Integer(), nullable=True),
        sa.Column("max_floating_ips", sa.Integer(), nullable=True),
        sa.Column("max_security_groups", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    pass
